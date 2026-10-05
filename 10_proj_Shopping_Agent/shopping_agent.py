import base64
import json
import os
import sqlite3
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import HumanMessage
from langchain_groq import ChatGroq

from reviews_api import get_product_rating

load_dotenv()

DB_PATH = os.path.join(os.path.dirname(__file__), "store.db")
DEFAULT_USER = "guest"

llm = ChatGroq(model="qwen/qwen3.8-27b", temperature=0, max_tokens=800)
vision_llm = ChatGroq(model="llama-3.1-8b-instant", temperature=0)


# ---------------------------------------------------------------------------
# User context & database setup
# ---------------------------------------------------------------------------


@dataclass
class ShopperContext:
    """Per-invocation context passed to tools. Never visible to or set by the LLM."""

    user_id: str = DEFAULT_USER


def _user_id(runtime: ToolRuntime) -> str:
    return getattr(runtime.context, "user_id", None) or DEFAULT_USER


def init_db() -> None:
    """Add per-user columns/tables needed for order history and preferences."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    order_columns = [row[1] for row in cursor.execute("PRAGMA table_info(orders)")]
    if "user_id" not in order_columns:
        cursor.execute(
            f"ALTER TABLE orders ADD COLUMN user_id TEXT NOT NULL DEFAULT '{DEFAULT_USER}'"
        )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id      TEXT PRIMARY KEY,
            organic_only INTEGER,
            max_price    REAL,
            min_rating   REAL,
            updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    conn.close()


def load_preferences(user_id: str) -> dict:
    """Return the user's saved preferences; unset preferences are omitted."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT organic_only, max_price, min_rating FROM user_preferences WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    conn.close()

    if not row:
        return {}
    prefs = {}
    if row[0] is not None:
        prefs["organic_only"] = bool(row[0])
    if row[1] is not None:
        prefs["max_price"] = row[1]
    if row[2] is not None:
        prefs["min_rating"] = row[2]
    return prefs


init_db()


# ---------------------------------------------------------------------------
# Guardrail
# ---------------------------------------------------------------------------


def is_shopping_related(message: str) -> bool:
    """Return True if the message is about shopping/products/orders."""
    check = llm.invoke(
        [
            HumanMessage(
                content=(
                    "Is the following user message related to shopping, products, orders, "
                    "or browsing a store? Answer ONLY 'yes' or 'no'.\n\n"
                    f"Message: {message}"
                )
            )
        ]
    )
    return "yes" in check.content.strip().lower()


def run_agent(user_message: str, user_id: str = DEFAULT_USER):
    """Run the agent, but only if the message passes the shopping guardrail."""
    if not is_shopping_related(user_message):
        print(
            "I'm a shopping assistant, so I can only help with finding and "
            "ordering products. What are you looking to buy today?"
        )
        return

    result = agent.invoke(
        {"messages": [{"role": "user", "content": user_message}]},
        context=ShopperContext(user_id=user_id),
    )
    print(result["messages"][-1].content)


@tool
def search_products(
    query: str, max_price: Optional[float] = None, is_organic: Optional[bool] = None
) -> str:
    """
    Search the product database by keyword (matched against name, description, and category).
    Optionally filter by maximum price and/or organic status.
    Returns a JSON array of matching products, each with: id, name, category, price,
    description, is_organic.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    sql = "SELECT id, name, category, price, description, is_organic FROM products WHERE 1=1"
    params: list = []

    if query:
        sql += " AND (name LIKE ? OR description LIKE ? OR category LIKE ?)"
        like = f"%{query}%"
        params.extend([like, like, like])

    if max_price is not None:
        sql += " AND price <= ?"
        params.append(max_price)

    if is_organic is not None:
        sql += " AND is_organic = ?"
        params.append(1 if is_organic else 0)

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()

    products = [
        {
            "id": row[0],
            "name": row[1],
            "category": row[2],
            "price": row[3],
            "description": row[4],
            "is_organic": bool(row[5]),
        }
        for row in rows
    ]
    return json.dumps(products)


@tool
def get_rating(product_id: int) -> str:
    """
    Get the average customer rating and total review count for a product by its ID.
    Returns a JSON object with: product_id, average_rating, review_count.
    """
    result = get_product_rating(product_id)
    return json.dumps(result)


@tool
def checkout(product_id: int, runtime: ToolRuntime) -> str:
    """
    Place an order for the given product ID. Saves the order to the database and returns
    a confirmation message with the order ID, product name, and price.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name, price FROM products WHERE id = ?", (product_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        return f"Error: product with ID {product_id} not found."

    name, price = row
    cursor.execute(
        "INSERT INTO orders (product_id, product_name, price, user_id) VALUES (?, ?, ?, ?)",
        (product_id, name, price, _user_id(runtime)),
    )
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return (
        f"Order #{order_id} confirmed! '{name}' has been successfully ordered for ${price:.2f}. "
        f"Your order will arrive in 3-5 business days. Thank you for shopping with us!"
    )


@tool
def describe_product_image(image_path: str) -> str:
    """
    Analyze a product image and return its key attributes as a JSON object.
    Use this when the user uploads a photo of a product they are interested in.
    The returned attributes can be used directly with search_products.
    """
    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode()

    ext = os.path.splitext(image_path)[1].lower().lstrip(".")
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"

    message = HumanMessage(
        content=[
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{image_data}"},
            },
            {
                "type": "text",
                "text": (
                    "Look at this product image and extract its key attributes. "
                    "Return ONLY a JSON object with these fields:\n"
                    "- product_type: what kind of product it is (e.g. honey, olive oil, almonds)\n"
                    "- search_query: a short keyword to search for it (e.g. 'honey', 'olive oil')\n"
                    "- is_organic: true if the label says organic, false if not, null if unclear\n"
                    "- description: one sentence describing the product"
                ),
            },
        ]
    )

    response = vision_llm.invoke([message])
    return response.content


def load_order_history(user_id: str, limit: int = 20) -> dict:
    """Return the user's orders (newest first), order count, and total spent."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """
        SELECT id, product_id, product_name, price, ordered_at
        FROM orders WHERE user_id = ?
        ORDER BY id DESC LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    count, total = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(price), 0) FROM orders WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    conn.close()

    orders = [
        {
            "order_id": row[0],
            "product_id": row[1],
            "product_name": row[2],
            "price": row[3],
            "ordered_at": row[4],
        }
        for row in rows
    ]
    return {"orders": orders, "order_count": count, "total_spent": round(total, 2)}


@tool
def get_order_history(runtime: ToolRuntime, limit: int = 20) -> str:
    """
    Get the current user's past orders, newest first.
    Returns a JSON object with: orders (each with order_id, product_id, product_name,
    price, ordered_at), order_count, and total_spent.
    """
    return json.dumps(load_order_history(_user_id(runtime), limit))


@tool
def get_preferences(runtime: ToolRuntime) -> str:
    """
    Get the current user's saved shopping preferences.
    Returns a JSON object with any of: organic_only, max_price, min_rating.
    An empty object means no preferences are saved.
    """
    return json.dumps(load_preferences(_user_id(runtime)))


@tool
def update_preferences(
    runtime: ToolRuntime,
    organic_only: Optional[bool] = None,
    max_price: Optional[float] = None,
    min_rating: Optional[float] = None,
    clear: Optional[list[str]] = None,
) -> str:
    """
    Save the current user's lasting shopping preferences so they apply in future sessions.
    Only pass the fields the user wants to set; omitted fields keep their saved value.
    To remove a preference, list its name in `clear` (any of: organic_only, max_price,
    min_rating). Returns the full set of preferences after the update.
    """
    user_id = _user_id(runtime)
    prefs = load_preferences(user_id)

    for name in clear or []:
        prefs.pop(name, None)
    if organic_only is not None:
        prefs["organic_only"] = organic_only
    if max_price is not None:
        prefs["max_price"] = max_price
    if min_rating is not None:
        prefs["min_rating"] = min_rating

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO user_preferences (user_id, organic_only, max_price, min_rating, updated_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
            organic_only = excluded.organic_only,
            max_price    = excluded.max_price,
            min_rating   = excluded.min_rating,
            updated_at   = excluded.updated_at
        """,
        (
            user_id,
            None if "organic_only" not in prefs else int(prefs["organic_only"]),
            prefs.get("max_price"),
            prefs.get("min_rating"),
        ),
    )
    conn.commit()
    conn.close()
    return json.dumps(prefs)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

agent = create_agent(
    tools=[
        search_products,
        get_rating,
        checkout,
        describe_product_image,
        get_order_history,
        get_preferences,
        update_preferences,
    ],
    model=llm,
    context_schema=ShopperContext,
    system_prompt=(
        "You are a helpful shopping assistant. Follow these rules strictly.\n\n"
        "PREFERENCES — the user may have lasting preferences (organic_only, max_price, "
        "min_rating):\n"
        "- Before your first search in a conversation, call get_preferences once.\n"
        "- Apply saved preferences as default filters on every search. If the user asks "
        "for something different in a specific request, their request wins for that search "
        "only — do not change the saved preference.\n"
        "- Call update_preferences ONLY when the user states a lasting preference "
        "(e.g. 'I always want organic', 'never show me anything over $20', 'from now on "
        "only 4+ stars') or asks to remove one ('I don't need organic anymore'). "
        "A one-off request like 'honey under $10' is NOT a lasting preference.\n"
        "- After saving, briefly confirm what you will remember.\n"
        "- When you apply saved preferences, mention them in one short line above the "
        "results (e.g. 'Using your saved preferences: organic only, under $20').\n\n"
        "ORDER HISTORY — when the user asks about past orders, spending, or what they "
        "bought before:\n"
        "1. Call get_order_history.\n"
        "2. Summarize in plain text: number of orders, total spent, and a list of items "
        "with price and date. If there are none, say so.\n"
        "3. To reorder an item, the user must confirm; then use its product_id with checkout.\n\n"
        "IMAGE SEARCH — when the user provides an image path:\n"
        "1. Call describe_product_image with the path to identify the product.\n"
        "2. Use the returned search_query and is_organic to call search_products.\n"
        "3. Continue with the BROWSING flow from step 2 onwards.\n\n"
        "BROWSING — when the user describes what they want to buy:\n"
        "1. Call search_products to find matching items (apply the user's filters, "
        "falling back to saved preferences).\n"
        "2. For each candidate, call get_rating to retrieve its average rating.\n"
        "3. Filter by the minimum rating from the request or saved preferences.\n"
        "4. Present qualifying products as a numbered list. For each item use this exact format "
        "   (plain text, no backticks, no code blocks, no bold, no italic):\n\n"
        "   #<number>. <name> (ID:<product_id>) — $<price> ★<rating> — <organic or non-organic>\n\n"
        "   Add a blank line between each product entry for readability. "
        "   Always include (ID:X) so you can reference it later.\n"
        "5. If only one product qualifies, still show it in the list and ask: "
        "   'Would you like to order it? Just say yes or give me the number.'\n"
        "6. Do NOT call checkout at this stage.\n\n"
        "ORDERING — when the user confirms they want to buy (e.g. 'yes', 'sure', 'go ahead', "
        "'order number 2', 'the first one', 'get me #3'):\n"
        "1. Look at your previous message to find the (ID:X) for the chosen product "
        "   (if only one was listed and the user says 'yes', use that product's ID).\n"
        "2. Call checkout with that product_id (the number from (ID:X)).\n"
        "3. Confirm the order to the user in plain text.\n\n"
        "Never place an order unless the user explicitly confirms. "
        "Never guess a product_id — always take it from the (ID:X) in your own previous message "
        "or from a get_order_history result."
    ),
)

if __name__ == "__main__":
    print("--- Test 1: off-topic (should be redirected) ---")
    run_agent("Write me a poem about the sea.")

    print("\n--- Test 2: shopping (should run normally) ---")
    run_agent("I want to buy organic honey with 4.5+ rating and less than $20 price.")

    print("\n--- Test 3: order history ---")
    run_agent("What have I ordered before?")

    print("\n--- Test 4: save a lasting preference ---")
    run_agent("From now on I always want organic products under $20.", user_id="demo")

    print("\n--- Test 5: preference applied in a new session ---")
    run_agent("Show me some olive oil.", user_id="demo")

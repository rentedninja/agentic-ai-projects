import html
import os
import re
import sqlite3
import tempfile
from datetime import datetime

import streamlit as st

from shopping_agent import (
    DB_PATH,
    DEFAULT_USER,
    ShopperContext,
    agent,
    load_order_history,
    load_preferences,
)

IMAGE_PROMPT_PREFIX = "I uploaded a product image"

CATEGORY_STYLE = {
    "honey": ("🍯", "#FCEFD4"),
    "oil": ("🫒", "#EEF2D9"),
    "nuts": ("🥜", "#F3E4D3"),
    "seeds": ("🌻", "#FBF1C9"),
    "grains": ("🌾", "#F2EAD3"),
    "tea": ("🍵", "#E3EFE1"),
    "coffee": ("☕", "#EBDFD5"),
    "snacks": ("🍪", "#F7E2DA"),
    "dairy-alt": ("🥛", "#E6EEF3"),
}

SUGGESTIONS = [
    "Organic honey under $20 with 4.5+ stars",
    "What's the best-rated olive oil?",
    "What have I ordered before?",
    "From now on, only show me organic products",
]

# Matches the agent's list format: #<n>. <name> (ID:<id>) — $<price> ★<rating> — <organic>
PRODUCT_LINE = re.compile(
    r"^\s*#?(\d+)[.)]\s*(.+?)\s*\(ID:\s*(\d+)\)\s*[—–-]+\s*\$?\s*([\d.]+)"
    r"(?:\s*★\s*([\d.]+))?"
)

# ---------------------------------------------------------------------------
# Page config & styles
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Harvest & Co. Shopping Assistant", page_icon="🧺", layout="wide")

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Inter:wght@400;500;600;700&display=swap');

.stApp { font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif; }
[data-testid="stMainBlockContainer"] { max-width: 1080px; padding-top: 4rem; }
[data-testid="stSidebar"] .section-label { margin-top: 1.4rem; }

/* Hero */
.hero {
  border-radius: 20px; padding: 2.1rem 2.4rem; margin-bottom: 1.5rem; color: #FBF8F2;
  background:
    radial-gradient(circle at 88% 18%, rgba(232,169,59,.42), transparent 42%),
    linear-gradient(135deg, #24553F 0%, #2F6B4F 55%, #3D8261 100%);
  box-shadow: 0 12px 32px -14px rgba(36,85,63,.55);
}
.hero-eyebrow { text-transform: uppercase; letter-spacing: .14em; font-size: .72rem; font-weight: 600; color: #F3D49A; }
.hero-title { font-family: 'Fraunces', Georgia, serif; font-weight: 700; font-size: 2.4rem; line-height: 1.1; margin: .45rem 0 .6rem; color: #FFFDF8; }
.hero-sub { color: rgba(255,253,248,.86); font-size: 1.02rem; max-width: 580px; }
.hero-chips { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: 1.25rem; }
.hero-chips span { background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.22); padding: .35rem .8rem; border-radius: 999px; font-size: .82rem; }
.hero.compact { padding: 1rem 1.5rem; margin-bottom: 1rem; }
.hero.compact .hero-title { font-size: 1.45rem; margin: .15rem 0 0; }

.section-label { font-size: .74rem; letter-spacing: .12em; text-transform: uppercase; color: #7A6F5C; font-weight: 600; margin: .75rem 0 .5rem; }

/* Product cards */
.pcard-tile { height: 92px; border-radius: 12px; display: flex; align-items: center; justify-content: center; font-size: 2.5rem; margin-bottom: .7rem; }
.pcard-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: .3rem; }
.pcard-rank { font-size: .75rem; font-weight: 600; color: #7A6F5C; }
.pcard-name { font-family: 'Fraunces', Georgia, serif; font-weight: 600; font-size: 1.06rem; line-height: 1.25; color: #1F2A24; min-height: 2.6em; }
.pcard-meta { display: flex; justify-content: space-between; align-items: baseline; margin: .5rem 0 .6rem; }
.pcard-price { font-size: 1.3rem; font-weight: 700; color: #24553F; }
.pcard-stars { font-size: .9rem; font-weight: 600; color: #B7791F; }
.badge { display: inline-block; font-size: .7rem; font-weight: 600; padding: .16rem .6rem; border-radius: 999px; }
.badge.organic { background: #E3F0E7; color: #24553F; }
.badge.conv { background: #EFE8DA; color: #7A6F5C; }
.pref-note { display: inline-block; background: #FFF4DF; color: #8A5A12; border: 1px solid #F1D9A8; padding: .3rem .8rem; border-radius: 999px; font-size: .85rem; margin-bottom: .6rem; }

/* Sidebar */
.brand { font-family: 'Fraunces', Georgia, serif; font-size: 1.4rem; font-weight: 700; color: #24553F; }
.brand-sub { font-size: .8rem; color: #7A6F5C; margin-top: -.1rem; }
.profile { display: flex; gap: .75rem; align-items: center; background: #FFFFFF; border: 1px solid #E4DCCB; border-radius: 14px; padding: .75rem .9rem; margin: 1rem 0 .25rem; }
.avatar { width: 40px; height: 40px; flex: none; border-radius: 50%; background: #2F6B4F; color: #FFFFFF; display: flex; align-items: center; justify-content: center; font-weight: 700; }
.profile small { display: block; color: #7A6F5C; font-size: .72rem; }
.profile b { font-size: .98rem; color: #1F2A24; }
.pills { display: flex; flex-wrap: wrap; gap: .35rem; }
.pills span { background: #E3F0E7; color: #24553F; font-size: .78rem; font-weight: 600; padding: .22rem .65rem; border-radius: 999px; }
.hint { font-size: .8rem; color: #7A6F5C; line-height: 1.45; }
.stats { display: flex; gap: .5rem; margin-bottom: .4rem; }
.stat { flex: 1; background: #FFFFFF; border: 1px solid #E4DCCB; border-radius: 12px; padding: .55rem .75rem; }
.stat b { display: block; font-size: 1.15rem; color: #24553F; }
.stat small { color: #7A6F5C; font-size: .72rem; }
.order-row { display: flex; justify-content: space-between; gap: .5rem; font-size: .84rem; padding: .45rem 0; border-bottom: 1px dashed #E4DCCB; color: #1F2A24; }
.order-row small { display: block; color: #7A6F5C; font-size: .72rem; }
</style>
""",
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def queue_prompt(prompt: str) -> None:
    st.session_state.pending_prompt = prompt


def on_aisle_pick() -> None:
    aisle = st.session_state.aisle
    if aisle:
        queue_prompt(f"Show me your best {aisle.split(' ', 1)[1].lower()}")
    st.session_state.aisle = None


def money(value: float) -> str:
    # HTML entity keeps Streamlit from treating "$...$" as LaTeX
    return f"&#36;{value:,.2f}"


@st.cache_data(ttl=300)
def product_details(product_ids: tuple[int, ...]) -> dict[int, dict]:
    conn = sqlite3.connect(DB_PATH)
    placeholders = ",".join("?" * len(product_ids))
    rows = conn.execute(
        f"SELECT id, category, is_organic FROM products WHERE id IN ({placeholders})",
        product_ids,
    ).fetchall()
    conn.close()
    return {r[0]: {"category": r[1], "is_organic": bool(r[2])} for r in rows}


def parse_reply(content: str) -> tuple[list[str], list[dict], list[str]]:
    """Split an agent reply into text before the product list, products, and text after."""
    before, after, products = [], [], []
    for line in content.splitlines():
        match = PRODUCT_LINE.match(line)
        if match:
            products.append(
                {
                    "rank": match.group(1),
                    "name": match.group(2).strip(" *"),
                    "id": int(match.group(3)),
                    "price": float(match.group(4)),
                    "rating": match.group(5),
                }
            )
        elif products:
            after.append(line)
        else:
            before.append(line)
    return before, products, after


def render_text(lines: list[str]) -> None:
    for chunk in "\n".join(lines).strip().split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.lower().startswith("using your saved preferences"):
            st.markdown(f'<div class="pref-note">✨ {html.escape(chunk)}</div>', unsafe_allow_html=True)
        else:
            st.markdown(chunk.replace("$", r"\$"))


def render_product_cards(products: list[dict], key: str, interactive: bool) -> None:
    details = product_details(tuple(p["id"] for p in products))
    for row_start in range(0, len(products), 3):
        cols = st.columns(3)
        for col, product in zip(cols, products[row_start : row_start + 3]):
            info = details.get(product["id"], {})
            emoji, tint = CATEGORY_STYLE.get(info.get("category"), ("🛍️", "#EFE8DA"))
            badge = (
                '<span class="badge organic">Organic</span>'
                if info.get("is_organic")
                else '<span class="badge conv">Conventional</span>'
            )
            stars = f'★ {product["rating"]}' if product["rating"] else ""
            with col, st.container(border=True):
                st.markdown(
                    f"""
<div class="pcard-tile" style="background:{tint}">{emoji}</div>
<div class="pcard-top"><span class="pcard-rank">#{product["rank"]}</span>{badge}</div>
<div class="pcard-name">{html.escape(product["name"])}</div>
<div class="pcard-meta"><span class="pcard-price">{money(product["price"])}</span><span class="pcard-stars">{stars}</span></div>
""",
                    unsafe_allow_html=True,
                )
                if interactive:
                    st.button(
                        "Order this",
                        key=f"order-{key}-{product['id']}",
                        type="primary",
                        width="stretch",
                        on_click=queue_prompt,
                        args=(
                            f"Yes, please order #{product['rank']} — "
                            f"{product['name']} (ID:{product['id']}).",
                        ),
                    )


def render_message(msg: dict, key: str, interactive: bool) -> None:
    if msg["role"] == "user":
        with st.chat_message("user", avatar="🧑"):
            if msg["content"].startswith(IMAGE_PROMPT_PREFIX):
                filename = os.path.basename(msg["content"].split("Image path:")[-1].strip())
                st.markdown(f"📷 Searching by photo · `{filename}`")
            else:
                st.markdown(msg["content"].replace("$", r"\$"))
        return

    with st.chat_message("assistant", avatar="🧺"):
        before, products, after = parse_reply(msg["content"])
        if re.search(r"order #\d+.*confirmed", msg["content"], re.IGNORECASE):
            st.success(msg["content"].replace("$", r"\$"), icon="🎉")
            return
        render_text(before)
        if products:
            render_product_cards(products, key, interactive)
        render_text(after)


def format_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%b %d, %Y")
    except ValueError:
        return value


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []

# ---------------------------------------------------------------------------
# Sidebar — profile, preferences, orders, shop by photo
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(
        '<div class="brand">🧺 Harvest &amp; Co.</div>'
        '<div class="brand-sub">Organic pantry · AI-assisted shopping</div>',
        unsafe_allow_html=True,
    )

    user_id = (
        st.text_input("Shopper name", value=DEFAULT_USER, label_visibility="collapsed",
                      placeholder="Your name").strip().lower()
        or DEFAULT_USER
    )

    # Each shopper gets their own conversation
    if st.session_state.get("user_id") != user_id:
        st.session_state.user_id = user_id
        st.session_state.messages = []

    st.markdown(
        f'<div class="profile"><div class="avatar">{html.escape(user_id[0].upper())}</div>'
        f"<div><small>Shopping as</small><b>{html.escape(user_id.title())}</b></div></div>",
        unsafe_allow_html=True,
    )

    st.markdown('<div class="section-label">Your preferences</div>', unsafe_allow_html=True)
    prefs = load_preferences(user_id)
    pills = []
    if prefs.get("organic_only"):
        pills.append("🌱 Organic only")
    elif prefs.get("organic_only") is False:
        pills.append("Organic or conventional")
    if "max_price" in prefs:
        pills.append(f"Under &#36;{prefs['max_price']:g}")
    if "min_rating" in prefs:
        pills.append(f"★ {prefs['min_rating']:g}+")
    if pills:
        st.markdown(
            '<div class="pills">' + "".join(f"<span>{p}</span>" for p in pills) + "</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="hint">Nothing saved yet. Say <i>"I always want organic"</i> '
            "and I'll remember it next time.</div>",
            unsafe_allow_html=True,
        )

    st.markdown('<div class="section-label">Recent orders</div>', unsafe_allow_html=True)
    history = load_order_history(user_id, limit=4)
    if history["order_count"]:
        st.markdown(
            f'<div class="stats">'
            f'<div class="stat"><b>{history["order_count"]}</b><small>orders</small></div>'
            f'<div class="stat"><b>{money(history["total_spent"])}</b><small>spent</small></div>'
            f"</div>"
            + "".join(
                f'<div class="order-row"><div>{html.escape(o["product_name"])}'
                f'<small>{format_date(o["ordered_at"])}</small></div>'
                f'<div>{money(o["price"])}</div></div>'
                for o in history["orders"]
            ),
            unsafe_allow_html=True,
        )
    else:
        st.markdown('<div class="hint">No orders yet.</div>', unsafe_allow_html=True)

    st.markdown('<div class="section-label">Shop by photo</div>', unsafe_allow_html=True)
    uploaded_file = st.file_uploader(
        "Upload a product photo",
        type=["jpg", "jpeg", "png", "webp"],
        label_visibility="collapsed",
    )
    if uploaded_file:
        st.image(uploaded_file, width="stretch")
        if st.button("Find similar products", type="primary", width="stretch"):
            suffix = os.path.splitext(uploaded_file.name)[1] or ".jpg"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(uploaded_file.getvalue())
            queue_prompt(
                f"{IMAGE_PROMPT_PREFIX}. Please analyze it and find similar products "
                f"in the store. Image path: {tmp.name}"
            )

    if st.session_state.messages:
        st.divider()
        if st.button("Start a new chat", icon=":material/refresh:", width="stretch"):
            st.session_state.messages = []
            st.rerun()

context = ShopperContext(user_id=user_id)

# ---------------------------------------------------------------------------
# Main — hero, starters, conversation
# ---------------------------------------------------------------------------
if st.session_state.messages:
    st.markdown(
        '<div class="hero compact"><div class="hero-eyebrow">AI shopping assistant</div>'
        '<div class="hero-title">What else can I find for you?</div></div>',
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        f"""
<div class="hero">
  <div class="hero-eyebrow">AI shopping assistant</div>
  <div class="hero-title">Good food, found for you.</div>
  <div class="hero-sub">Hi {html.escape(user_id.title())} — tell me what you're after. I'll search the
  shelves, check what other shoppers think, and place the order when you're ready.</div>
  <div class="hero-chips">
    <span>🔍 Smart search</span><span>⭐ Rating checks</span>
    <span>🛒 One-tap ordering</span><span>🧠 Remembers what you like</span>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    st.markdown('<div class="section-label">Try asking</div>', unsafe_allow_html=True)
    cols = st.columns(2)
    for i, suggestion in enumerate(SUGGESTIONS):
        cols[i % 2].button(
            suggestion.replace("$", r"\$"),
            key=f"suggestion-{i}",
            width="stretch",
            on_click=queue_prompt,
            args=(suggestion,),
        )

    st.markdown('<div class="section-label">Browse the aisles</div>', unsafe_allow_html=True)
    st.pills(
        "Aisles",
        [f"{emoji} {name.replace('-', ' ').title()}" for name, (emoji, _) in CATEGORY_STYLE.items()],
        key="aisle",
        on_change=on_aisle_pick,
        label_visibility="collapsed",
    )

last_index = len(st.session_state.messages) - 1
for i, msg in enumerate(st.session_state.messages):
    # Only the latest reply gets order buttons, so old lists can't trigger stale orders
    render_message(msg, key=str(i), interactive=i == last_index)

prompt = st.chat_input("Ask for a product, a price limit, a rating…")
if not prompt:
    prompt = st.session_state.pop("pending_prompt", None)

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    render_message(st.session_state.messages[-1], key="pending", interactive=False)

    is_image = prompt.startswith(IMAGE_PROMPT_PREFIX)
    with st.chat_message("assistant", avatar="🧺"):
        with st.spinner("Looking at your photo…" if is_image else "Searching the shelves…"):
            try:
                result = agent.invoke({"messages": st.session_state.messages}, context=context)
            except Exception as exc:
                st.error(f"Sorry — I couldn't reach the assistant right now. ({type(exc).__name__})")
                st.stop()
        response = result["messages"][-1].content.replace("`", "")

    st.session_state.messages.append({"role": "assistant", "content": response})
    st.rerun()

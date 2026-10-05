# Agentic AI Projects

Two LLM applications built with LangChain, Groq and Streamlit.

## 1. AI Shopping Agent — `10_proj_Shopping_Agent/`

A tool-calling agent that searches a product catalog, checks ratings, and places orders through natural conversation.

- **Tools:** `search_products` (SQLite search with price/organic filters), `get_rating`, `checkout`, `describe_product_image` (shop by photo)
- **Agent:** LangChain `create_agent` with a system prompt defining browse → rate → confirm → order flows
- **UI:** Streamlit chat with image upload

```bash
cd 10_proj_Shopping_Agent
streamlit run app.py
```

## 2. Telecom Support RAG — `11_Rag_telecom/`

A customer-care chatbot that answers questions using retrieval-augmented generation over three sources: FAQs, past support tickets, and a PDF guide.

- **Retrieval:** Chroma vector store with `all-MiniLM-L6-v2` embeddings; top-3 results from each collection are merged
- **Generation:** Qwen3 on Groq, instructed to answer only from retrieved context
- **UI:** Streamlit chat with streaming responses (or a CLI via `main.py`)

```bash
cd 11_Rag_telecom
python ingest_faq.py
python ingest_tickets.py
python ingest_pdf.py
streamlit run app.py
```

## Setup

Requires Python 3.14 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

Create a `.env` file in the repo root:

```
GROQ_API_KEY=your_key_here
```

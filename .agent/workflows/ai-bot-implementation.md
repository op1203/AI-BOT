---
description: How to implement an Advanced AI Bot with RAG and Gemini in Odoo
---

This workflow outlines the technical architecture and implementation steps for building a production-ready AI Bot that interacts with Odoo's Inventory module using RAG (Retrieval-Augmented Generation), Vector Databases, and Gemini.

### 1. Architecture Overview
- **Odoo Backend**: Python controllers for REST API and Bus for longpolling.
- **Vector Database**: Use `pgvector` (Postgres extension) or an external service like Pinecone to store embeddings of your products and stock data.
- **LLM**: Google Gemini Pro (via API) for processing natural language.
- **RAG Pipeline**: 
    - **Indexing**: Convert Odoo inventory data into embeddings and store them in the Vector DB.
    - **Retrieval**: When a user asks a question, find relevant stock data from the Vector DB.
    - **Generation**: Pass the user question + retrieved data + custom prompt to Gemini.

### 2. Implementation Steps

#### A. Data Vectorization (Indexing)
1. Install `pgvector` on your Odoo database instance.
2. Create a model in Odoo to store embeddings for `product.product`.
3. Use a Python script to iterate through products, generate embeddings using Gemini/OpenAI, and save them to the database.

#### B. Context Retrieval (RAG)
1. When a user sends a chat message, generate an embedding for that message.
2. Query the Vector DB for the top-k most similar product records.
3. Extract text details (names, descriptions, stock levels) from these records.

#### C. Gemini Integration
1. Format a "System Prompt" that defines the bot's personality and rules (e.g., "You are an inventory assistant...").
2. Construct the final prompt: `[System Prompt] + [Retrieved Inventory Context] + [User Query]`.
3. Call the Gemini API to get the final response.

#### D. Real-time Communication (Longpolling/Webhooks)
1. Use Odoo's `bus.bus` to send real-time updates to the chat widget.
2. Create a REST controller (`/api/inventory_ai/query`) for external integrations if needed.

### 3. Usage Example
To run the indexing script (example):
```bash
python3 /opt/custom_addons/inventory_ai/scripts/index_inventory.py
```

### 4. Customizing Prompts
Edit the `_get_ai_response` method in `controllers/main.py` or a dedicated configuration model to include your "Custom Prompt" which acts as the "Brain" of your AI.

---
// turbo
5. Re-run Odoo with the new logic
```bash
./odoo-bin --addons-path=addons/,/opt/custom_addons/ -u inventory_ai
```

# Property Agent Bot 🏠

An intelligent, AI-powered real estate management and property recommendation bot built with Python, Streamlit, SQLite, and local AI (Ollama).

## 🌟 Features

- **Conversational Real Estate Search**: Filter properties using natural language powered by a local Ollama LLM (`llama3.2:3b`).
- **Property Catalog & Listings**: Browse apartments, villas, and flats with rich details, images, and pricing.
- **CRM & Lead Management**: Track customer inquiries, lead statuses, schedule visits, and view analytics.
- **Property Management**: Add, update, and manage property listings with image upload support.
- **Fast Local Storage**: Lightweight SQLite database for zero-config data persistence.

---

## 📁 Project Structure

```text
property-agent-bot/
├── app.py                # Main Streamlit web application
├── init_db.py            # Database initialization script
├── requirements.txt      # Project dependencies
├── services/
│   ├── local_ai.py       # Ollama LLM integration for NLP filter extraction
│   └── property_search.py # Search engine & query parser
├── assets/
│   └── properties/       # Property sample images and user uploads
└── data/
    └── property_agent.db # SQLite database
```

---

## 🚀 Quick Start

### 1. Clone the repository
```bash
git clone https://github.com/manusonagara15-ctrl/Property_agent.git
cd Property_agent
```

### 2. Create and activate a virtual environment
```bash
python -m venv .venv

# Windows:
.\.venv\Scripts\activate

# macOS / Linux:
source .venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Initialize Database
```bash
python init_db.py
```

### 5. Run Application
```bash
streamlit run app.py
```

---

## 🤖 Optional: Local AI Setup (Ollama)

To enable conversational AI search:
1. Install [Ollama](https://ollama.ai/).
2. Pull the model:
   ```bash
   ollama pull llama3.2:3b
   ```
3. Ensure Ollama is running at `http://localhost:11434`.
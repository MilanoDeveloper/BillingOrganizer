# Billing Organizer API

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

A API usa `DATABASE_URL` ou, por padrão, `postgresql://postgres:abc123@localhost:5432/postgres`. O schema `financeiro` e suas tabelas devem existir antes da inicialização.

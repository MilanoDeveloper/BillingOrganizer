# Billing Organizer API

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

A API usa `DATABASE_URL` ou, por padrão, `postgresql://postgres:abc123@localhost:5432/postgres`. O schema `financeiro` e suas tabelas devem existir antes da inicialização.

## Autenticação

O primeiro acesso permite criar uma conta em `/api/auth/cadastro` ou fazer login em `/api/auth/login`. A API cria uma sessão em cookie HttpOnly válida por 30 dias. Categorias, lançamentos e importações são filtrados pelo usuário da sessão; `POST /api/auth/logout` encerra a sessão.

O cadastro exige nome, e-mail e senha com pelo menos 8 caracteres. A senha é armazenada com PBKDF2-SHA256, nunca em texto puro.

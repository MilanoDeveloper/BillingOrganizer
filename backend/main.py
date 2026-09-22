from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
import csv
import base64
import hashlib
import os
import re
import secrets
import unicodedata

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pypdf import PdfReader
import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:abc123@localhost:5432/postgres",
)
DEFAULT_USER_ID = 1
SESSION_COOKIE = "billing_session"
SESSION_DAYS = 30

app = FastAPI(title="Billing Organizer API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ManualTransaction(BaseModel):
    descricao: str = Field(min_length=1, max_length=255)
    valor: Decimal
    data: date = Field(default_factory=date.today)
    categoria: str = Field(default="Outros", max_length=100)
    metodo_pagamento: str | None = Field(default=None, max_length=50)


class TransactionUpdate(BaseModel):
    descricao: str = Field(min_length=1, max_length=255)
    categoria: str = Field(min_length=1, max_length=100)


class CategoryCreate(BaseModel):
    nome: str = Field(min_length=1, max_length=100)


class AccountCreate(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=5, max_length=255)
    senha: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    senha: str = Field(min_length=1, max_length=128)


CATEGORY_RULES = {
    "Investimentos": ("APLICACAO COFRINHOS", "INVESTIMENTO", "APLICACAO"),
    "Contas fixas": (
        "BOLETO BANCO VOLKSWAGEN",
        "BOLETO CONDOMINIO",
        "COMPANHIA DE GAS",
        "COMGAS",
        "PIX QRS TELEFONICA",
    ),
}


def connection():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
    return f"pbkdf2_sha256$240000${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(rounds))
        return secrets.compare_digest(base64.b64encode(digest).decode(), expected)
    except (ValueError, TypeError):
        return False


def create_session(response: Response, user_id: int) -> None:
    session_id = secrets.token_urlsafe(48)
    with connection() as conn:
        conn.execute(
            "INSERT INTO financeiro.sessoes (id, usuario_id, expira_em) VALUES (%s, %s, CURRENT_TIMESTAMP + INTERVAL '30 days')",
            (session_id, user_id),
        )
    response.set_cookie(SESSION_COOKIE, session_id, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax")


def current_user(request: Request) -> dict:
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id:
        raise HTTPException(status_code=401, detail="Faça login para continuar.")
    with connection() as conn:
        user = conn.execute(
            """
            SELECT u.id, u.nome, u.email
            FROM financeiro.sessoes s
            JOIN financeiro.usuarios u ON u.id = s.usuario_id
            WHERE s.id = %s AND s.expira_em > CURRENT_TIMESTAMP
            """,
            (session_id,),
        ).fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="Sua sessão expirou. Faça login novamente.")
    return user


def ensure_default_user() -> None:
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO financeiro.usuarios (id, nome, email, senha_hash)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (DEFAULT_USER_ID, "Usuário principal", "local@billing.organizer", "local"),
            )
            cursor.execute(
                "SELECT setval(pg_get_serial_sequence('financeiro.usuarios', 'id'), GREATEST((SELECT MAX(id) FROM financeiro.usuarios), 1))"
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS financeiro.categorias (
                    id SERIAL PRIMARY KEY,
                    usuario_id INT NOT NULL REFERENCES financeiro.usuarios(id) ON DELETE CASCADE,
                    nome VARCHAR(100) NOT NULL,
                    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uk_categoria_usuario_nome UNIQUE (usuario_id, nome)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS financeiro.importacoes (
                    id SERIAL PRIMARY KEY,
                    usuario_id INT NOT NULL REFERENCES financeiro.usuarios(id) ON DELETE CASCADE,
                    nome_arquivo VARCHAR(255) NOT NULL,
                    hash_arquivo VARCHAR(64) NOT NULL,
                    importado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uk_importacao_usuario_hash UNIQUE (usuario_id, hash_arquivo)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS financeiro.sessoes (
                    id VARCHAR(64) PRIMARY KEY,
                    usuario_id INT NOT NULL REFERENCES financeiro.usuarios(id) ON DELETE CASCADE,
                    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expira_em TIMESTAMP NOT NULL
                )
                """
            )
            cursor.executemany(
                """
                INSERT INTO financeiro.categorias (usuario_id, nome)
                VALUES (%s, %s)
                ON CONFLICT (usuario_id, nome) DO NOTHING
                """,
                [(DEFAULT_USER_ID, name) for name in ("Outros", "Moradia", "Alimentação", "Transporte", "Saúde", "Lazer", "Investimentos", "Contas fixas")],
            )


def parse_brazilian_amount(value: str) -> Decimal:
    normalized = value.strip().replace("R$", "")
    normalized = "".join(normalized.split())
    if "," in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    try:
        return Decimal(normalized)
    except InvalidOperation as error:
        raise ValueError(f"Valor inválido: {value}") from error


def normalize_description(description: str) -> str:
    """Remove a data de referência anexada ao fim da descrição do extrato."""
    return re.sub(r"\s*\d{2}/\d{2}$", "", description).strip()


def categorize_description(description: str) -> str:
    normalized = description.upper()
    for category, keywords in CATEGORY_RULES.items():
        if any(keyword in normalized for keyword in keywords):
            return category
    return "Outros"


def extract_pdf_text(content: bytes, filename: str) -> str:
    if filename.lower().endswith(".txt"):
        return content.decode("utf-8", errors="replace")
    try:
        reader = PdfReader(BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as error:
        raise HTTPException(status_code=400, detail="Não foi possível ler o PDF.") from error


def parse_csv_statement(content: bytes, origin: str, user_id: int) -> list[dict]:
    text = content.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t,")
    except csv.Error:
        dialect = csv.excel()
        dialect.delimiter = ";"
    rows = csv.DictReader(text.splitlines(), dialect=dialect)
    if not rows.fieldnames:
        raise HTTPException(status_code=400, detail="O CSV não possui cabeçalho.")
    headers = {
        re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", header.lower()).encode("ascii", "ignore").decode()) : header
        for header in rows.fieldnames if header
    }
    date_key = headers.get("data") or headers.get("date")
    description_key = headers.get("descricao") or headers.get("movimentacao") or headers.get("description") or headers.get("nome")
    amount_key = headers.get("valor") or headers.get("amount")
    payment_key = headers.get("meiodepagamento") or headers.get("metodopagamento") or headers.get("paymentmethod")
    if not date_key or not description_key or not amount_key:
        raise HTTPException(status_code=400, detail="O CSV deve conter as colunas data, descricao e valor.")
    transactions = []
    for row in rows:
        raw_date = (row.get(date_key) or "").strip()
        raw_description = normalize_description((row.get(description_key) or "").strip())
        raw_amount = (row.get(amount_key) or "").strip()
        if not raw_date or not raw_description or not raw_amount:
            continue
        try:
            transaction_date = datetime.strptime(raw_date, "%d/%m/%Y").date() if "/" in raw_date else date.fromisoformat(raw_date)
            amount = parse_brazilian_amount(raw_amount)
        except (ValueError, InvalidOperation) as error:
            raise HTTPException(status_code=400, detail=f"Linha inválida no CSV: {raw_date}, {raw_amount}.") from error
        transactions.append({
            "usuario_id": user_id,
            "data": transaction_date,
            "descricao": raw_description[:255],
            "valor": amount,
            "origem": origin[:100],
            "categoria": categorize_description(raw_description),
            "metodo_pagamento": (row.get(payment_key) or "CSV").strip() if payment_key else "CSV",
            "tipo_entrada": "CSV",
        })
    return transactions


def parse_statement(text: str, origin: str, user_id: int) -> list[dict]:
    transactions = []
    line_pattern = re.compile(
        r"^(?P<day>\d{2}/\d{2}/\d{4})\s+(?P<description>.+?)\s+(?P<amount>-?\d{1,3}(?:\.\d{3})*,\d{2})$"
    )
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        match = line_pattern.match(line)
        if not match or "SALDO DO DIA" in match.group("description").upper():
            continue
        description = normalize_description(match.group("description"))
        if not description:
            continue
        transactions.append(
            {
                "usuario_id": user_id,
                "data": datetime.strptime(match.group("day"), "%d/%m/%Y").date(),
                "descricao": description[:255],
                "valor": parse_brazilian_amount(match.group("amount")),
                "origem": origin[:100],
                "categoria": categorize_description(description),
                "metodo_pagamento": "Extrato",
                "tipo_entrada": "PDF",
            }
        )
    return transactions


def insert_transactions(transactions: list[dict]) -> int:
    if not transactions:
        return 0
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO financeiro.transacoes
                (usuario_id, data, descricao, valor, origem, categoria, metodo_pagamento, tipo_entrada)
                VALUES (%(usuario_id)s, %(data)s, %(descricao)s, %(valor)s, %(origem)s,
                        %(categoria)s, %(metodo_pagamento)s, %(tipo_entrada)s)
                """,
                transactions,
            )
    return len(transactions)


@app.on_event("startup")
def startup() -> None:
    ensure_default_user()


@app.get("/api/health")
def health() -> dict:
    with connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.post("/api/auth/cadastro", status_code=201)
def register(account: AccountCreate, response: Response) -> dict:
    email = account.email.strip().lower()
    name = account.nome.strip()
    if not name or not email:
        raise HTTPException(status_code=400, detail="Informe nome e e-mail.")
    try:
        with connection() as conn:
            user = conn.execute(
                "INSERT INTO financeiro.usuarios (nome, email, senha_hash) VALUES (%s, %s, %s) RETURNING id, nome, email",
                (name, email, hash_password(account.senha)),
            ).fetchone()
            with conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO financeiro.categorias (usuario_id, nome) VALUES (%s, %s)",
                    [(user["id"], category) for category in ("Outros", "Moradia", "Alimentação", "Transporte", "Saúde", "Lazer", "Investimentos", "Contas fixas")],
                )
    except UniqueViolation as error:
        raise HTTPException(status_code=409, detail="Já existe uma conta com esse e-mail.") from error
    create_session(response, user["id"])
    return user


@app.post("/api/auth/login")
def login(credentials: LoginRequest, response: Response) -> dict:
    with connection() as conn:
        user = conn.execute(
            "SELECT id, nome, email, senha_hash FROM financeiro.usuarios WHERE lower(email) = lower(%s)",
            (credentials.email.strip(),),
        ).fetchone()
    if not user or not verify_password(credentials.senha, user["senha_hash"]):
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos.")
    create_session(response, user["id"])
    return {"id": user["id"], "nome": user["nome"], "email": user["email"]}


@app.get("/api/auth/me")
def me(user: dict = Depends(current_user)) -> dict:
    return user


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict:
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        with connection() as conn:
            conn.execute("DELETE FROM financeiro.sessoes WHERE id = %s", (session_id,))
    response.delete_cookie(SESSION_COOKIE)
    return {"message": "Sessão encerrada."}


@app.get("/api/categorias")
def list_categories(user: dict = Depends(current_user)) -> list[dict]:
    with connection() as conn:
        return conn.execute(
            "SELECT id, nome FROM financeiro.categorias WHERE usuario_id = %s ORDER BY nome",
            (user["id"],),
        ).fetchall()


@app.post("/api/categorias", status_code=201)
def create_category(category: CategoryCreate, user: dict = Depends(current_user)) -> dict:
    name = category.nome.strip()
    try:
        with connection() as conn:
            return conn.execute(
                "INSERT INTO financeiro.categorias (usuario_id, nome) VALUES (%s, %s) RETURNING id, nome",
                (user["id"], name),
            ).fetchone()
    except UniqueViolation as error:
        raise HTTPException(status_code=409, detail="Essa categoria já existe.") from error


@app.get("/api/transacoes")
def list_transactions(
    data_inicio: date | None = Query(default=None),
    data_fim: date | None = Query(default=None),
    busca: str | None = Query(default=None, max_length=100),
    user: dict = Depends(current_user),
) -> list[dict]:
    if data_inicio and data_fim and data_inicio > data_fim:
        raise HTTPException(status_code=400, detail="A data inicial deve ser anterior à data final.")
    with connection() as conn:
        rows = conn.execute(
            """
                 SELECT id, data,
                     regexp_replace(descricao, '[[:space:]]*[0-9]{2}/[0-9]{2}$', '') AS descricao,
                     valor, origem, categoria, metodo_pagamento, tipo_entrada
            FROM financeiro.transacoes
            WHERE usuario_id = %s
              AND (%s::date IS NULL OR data >= %s::date)
              AND (%s::date IS NULL OR data <= %s::date)
              AND (%s::text IS NULL OR regexp_replace(descricao, '[[:space:]]*[0-9]{2}/[0-9]{2}$', '') ILIKE '%%' || %s::text || '%%')
            ORDER BY data DESC, id DESC
            LIMIT 100
            """,
            (user["id"], data_inicio, data_inicio, data_fim, data_fim, busca, busca),
        ).fetchall()
    return rows


@app.get("/api/resumo")
def summary(
    data_inicio: date | None = Query(default=None),
    data_fim: date | None = Query(default=None),
    busca: str | None = Query(default=None, max_length=100),
    user: dict = Depends(current_user),
) -> dict:
    if data_inicio and data_fim and data_inicio > data_fim:
        raise HTTPException(status_code=400, detail="A data inicial deve ser anterior à data final.")
    period_params = (user["id"], data_inicio, data_inicio, data_fim, data_fim, busca, busca)
    with connection() as conn:
        totals = conn.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN valor < 0 THEN ABS(valor) ELSE 0 END), 0) AS gastos,
              COALESCE(SUM(CASE WHEN valor > 0 THEN valor ELSE 0 END), 0) AS entradas,
              COUNT(*) AS quantidade
            FROM financeiro.transacoes
                        WHERE usuario_id = %s
                              AND (%s::date IS NULL OR data >= %s::date)
                              AND (%s::date IS NULL OR data <= %s::date)
                              AND (%s::text IS NULL OR regexp_replace(descricao, '[[:space:]]*[0-9]{2}/[0-9]{2}$', '') ILIKE '%%' || %s::text || '%%')
            """,
                        period_params,
        ).fetchone()
        categories = conn.execute(
            """
            SELECT categoria AS nome, COALESCE(SUM(ABS(valor)), 0) AS total
            FROM financeiro.transacoes
                        WHERE usuario_id = %s AND valor < 0
                              AND (%s::date IS NULL OR data >= %s::date)
                              AND (%s::date IS NULL OR data <= %s::date)
                              AND (%s::text IS NULL OR regexp_replace(descricao, '[[:space:]]*[0-9]{2}/[0-9]{2}$', '') ILIKE '%%' || %s::text || '%%')
            GROUP BY categoria
            ORDER BY total DESC
            """,
                        period_params,
        ).fetchall()
        grouped = conn.execute(
            """
                 SELECT regexp_replace(descricao, '[[:space:]]*[0-9]{2}/[0-9]{2}$', '') AS nome,
                     COALESCE(SUM(ABS(valor)), 0) AS total, COUNT(*) AS ocorrencias
            FROM financeiro.transacoes
                        WHERE usuario_id = %s AND valor < 0
                              AND (%s::date IS NULL OR data >= %s::date)
                              AND (%s::date IS NULL OR data <= %s::date)
                              AND (%s::text IS NULL OR regexp_replace(descricao, '[[:space:]]*[0-9]{2}/[0-9]{2}$', '') ILIKE '%%' || %s::text || '%%')
            GROUP BY regexp_replace(descricao, '[[:space:]]*[0-9]{2}/[0-9]{2}$', '')
            ORDER BY total DESC
            LIMIT 8
            """,
                        period_params,
        ).fetchall()
    return {"gastos": totals["gastos"], "entradas": totals["entradas"], "quantidade": totals["quantidade"], "categorias": categories, "maiores_gastos": grouped}


@app.patch("/api/transacoes/{transaction_id}")
def update_transaction(transaction_id: int, transaction: TransactionUpdate, user: dict = Depends(current_user)) -> dict:
    with connection() as conn:
        result = conn.execute(
            """
            WITH target AS (
                SELECT regexp_replace(descricao, '[[:space:]]*[0-9]{2}/[0-9]{2}$', '') AS nome_normalizado
                FROM financeiro.transacoes
                WHERE id = %s AND usuario_id = %s
            )
            UPDATE financeiro.transacoes AS t
            SET descricao = %s, categoria = %s
            WHERE t.usuario_id = %s
              AND regexp_replace(t.descricao, '[[:space:]]*[0-9]{2}/[0-9]{2}$', '') = (SELECT nome_normalizado FROM target)
            RETURNING t.id, t.data, t.descricao, t.valor, t.origem, t.categoria, t.metodo_pagamento, t.tipo_entrada
            """,
            (transaction_id, user["id"], transaction.descricao.strip(), transaction.categoria.strip(), user["id"]),
        ).fetchall()
    if not result:
        raise HTTPException(status_code=404, detail="Transação não encontrada.")
    return {"message": f"{len(result)} lançamentos atualizados.", "atualizados": len(result)}


@app.delete("/api/transacoes/{transaction_id}")
def delete_transaction(transaction_id: int, user: dict = Depends(current_user)) -> dict:
    with connection() as conn:
        result = conn.execute(
            """
            DELETE FROM financeiro.transacoes
            WHERE id = %s AND usuario_id = %s
            RETURNING id
            """,
            (transaction_id, user["id"]),
        ).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Transação não encontrada.")
    return {"message": "Lançamento excluído com sucesso."}


@app.delete("/api/transacoes")
def delete_all_transactions(user: dict = Depends(current_user)) -> dict:
    with connection() as conn:
        result = conn.execute(
            "DELETE FROM financeiro.transacoes WHERE usuario_id = %s RETURNING id",
            (user["id"],),
        ).fetchall()
        conn.execute(
            "DELETE FROM financeiro.importacoes WHERE usuario_id = %s",
            (user["id"],),
        )
    return {"message": f"{len(result)} lançamentos excluídos.", "excluidos": len(result)}


@app.post("/api/transacoes/manual", status_code=201)
def create_manual(transaction: ManualTransaction, user: dict = Depends(current_user)) -> dict:
    values = {
        "usuario_id": user["id"],
        "data": transaction.data,
        "descricao": transaction.descricao,
        "valor": -abs(transaction.valor),
        "origem": "Lançamento manual",
        "categoria": transaction.categoria,
        "metodo_pagamento": transaction.metodo_pagamento,
        "tipo_entrada": "MANUAL",
    }
    insert_transactions([values])
    return {"message": "Conta adicionada com sucesso."}


@app.post("/api/importar", status_code=201)
async def import_statement(file: UploadFile = File(...), user: dict = Depends(current_user)) -> dict:
    if not file.filename or not file.filename.lower().endswith((".pdf", ".txt", ".csv")):
        raise HTTPException(status_code=400, detail="Envie um arquivo PDF, TXT ou CSV.")
    content = await file.read()
    transactions = parse_csv_statement(content, file.filename, user["id"]) if file.filename.lower().endswith(".csv") else parse_statement(extract_pdf_text(content, file.filename), file.filename, user["id"])
    file_hash = hashlib.sha256(content).hexdigest()
    try:
        with connection() as conn:
            conn.execute(
                "INSERT INTO financeiro.importacoes (usuario_id, nome_arquivo, hash_arquivo) VALUES (%s, %s, %s)",
                (user["id"], file.filename[:255], file_hash),
            )
            if transactions:
                with conn.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO financeiro.transacoes
                        (usuario_id, data, descricao, valor, origem, categoria, metodo_pagamento, tipo_entrada)
                        VALUES (%(usuario_id)s, %(data)s, %(descricao)s, %(valor)s, %(origem)s,
                                %(categoria)s, %(metodo_pagamento)s, %(tipo_entrada)s)
                        """,
                        transactions,
                    )
            imported = len(transactions)
    except UniqueViolation as error:
        raise HTTPException(status_code=409, detail="Este arquivo já foi importado anteriormente.") from error
    return {"message": f"{imported} lançamentos importados.", "importados": imported}

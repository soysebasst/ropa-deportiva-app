from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, send_from_directory, session, Response
import csv, io
from werkzeug.utils import secure_filename
from werkzeug.exceptions import NotFound
from pathlib import Path
from datetime import datetime
import sqlite3, uuid, os, shutil, hmac

from reportlab.lib.colors import HexColor, white
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.pdfbase.pdfmetrics import stringWidth

try:
    from PIL import Image, ImageOps
except ImportError:  # Pillow no instalado todavía (ver requirements.txt)
    Image = None
    ImageOps = None

BASE = Path(__file__).resolve().parent

# En local, DATA_DIR no se configura y todo queda donde siempre (junto al
# código). En producción (Render, Railway, etc.) se define la variable de
# entorno DATA_DIR apuntando al disco persistente, y la base de datos y las
# imágenes se guardan ahí en vez de en el propio código de la app.
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB = DATA_DIR / "inventario.db"
UPLOADS = DATA_DIR / "uploads"
UPLOADS.mkdir(exist_ok=True)

# Carpeta con una copia de los datos reales, incluida una sola vez en el
# repositorio, para poblar el disco persistente la primera vez que la app
# arranca en un servidor nuevo. Una vez el disco tiene su propio
# inventario.db, esta copia de siembra nunca se vuelve a tocar.
SEED_DIR = BASE / "seed_data"


def seed_data_if_needed():
    if DATA_DIR.resolve() == BASE.resolve():
        return  # desarrollo local: nada que sembrar
    if DB.exists():
        return  # el disco ya tiene datos reales, no los pisamos
    seed_db = SEED_DIR / "inventario.db"
    if seed_db.exists():
        shutil.copy2(seed_db, DB)
    seed_uploads = SEED_DIR / "uploads"
    if seed_uploads.exists():
        for item in seed_uploads.iterdir():
            dest = UPLOADS / item.name
            if not dest.exists():
                shutil.copy2(item, dest)


seed_data_if_needed()

app = Flask(__name__)


def _asset_version(filename):
    """Devuelve la fecha de modificación del archivo estático como número,
    para usarla como ?v= en los enlaces a CSS/JS y así forzar a los
    navegadores (y al caché de PythonAnywhere) a bajar la versión nueva
    cada vez que se publica un cambio, en vez de quedarse con la vieja."""
    try:
        path = Path(app.static_folder) / filename
        return int(path.stat().st_mtime)
    except OSError:
        return 1


app.jinja_env.globals["asset_version"] = _asset_version

_default_secret = "cambia-esta-clave"
app.secret_key = os.environ.get("SECRET_KEY", _default_secret)
if app.secret_key == _default_secret:
    print("ADVERTENCIA: usando SECRET_KEY de ejemplo. Antes de publicar en internet, define la variable de entorno SECRET_KEY con un valor propio y secreto.")

app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024

# ---------------------------------------------------------------------------
# Acceso al administrador (usuario y contraseña por variables de entorno)
# ---------------------------------------------------------------------------
_default_admin_user = "admin"
_default_admin_pass = "admin"
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", _default_admin_user)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", _default_admin_pass)
if ADMIN_PASSWORD == _default_admin_pass:
    print("ADVERTENCIA: usando ADMIN_USERNAME/ADMIN_PASSWORD de ejemplo (admin/admin). Antes de publicar en internet, define tus propias credenciales por variable de entorno.")

# Rutas que cualquiera puede ver sin iniciar sesión: el catálogo público,
# su API de solo lectura, las imágenes subidas, los archivos estáticos y la
# propia pantalla de login. Todo lo demás exige haber iniciado sesión.
PUBLIC_PATH_PREFIXES = (
    "/inicio", "/catalogo", "/api/catalogo", "/mayoristas", "/api/mayoristas",
    "/uploads/", "/static/", "/login",
    "/politica-de-privacidad", "/terminos-y-condiciones",
    "/robots.txt", "/sitemap.xml",
    "/pedido/", "/api/pedido",
)


@app.before_request
def require_admin_login():
    path = request.path
    if path.startswith(PUBLIC_PATH_PREFIXES):
        return
    if session.get("admin_logged_in"):
        return
    # Antes de mandar a login, confirmamos que la ruta exista de verdad: si no
    # corresponde a ninguna vista registrada, dejamos que Flask la trate como
    # un 404 normal (con nuestra página personalizada) en vez de redirigir a
    # una pantalla de login que no tiene nada que ver con lo que se buscaba.
    try:
        app.url_map.bind_to_environ(request.environ).match()
    except NotFound:
        return
    except Exception:
        pass
    return redirect(url_for("login", next=path))


@app.errorhandler(404)
def not_found(error):
    return render_template("notfound.html"), 404


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = request.form.get("username", "")
        pw = request.form.get("password", "")
        ok_user = hmac.compare_digest(user, ADMIN_USERNAME)
        ok_pass = hmac.compare_digest(pw, ADMIN_PASSWORD)
        if ok_user and ok_pass:
            session.clear()
            session["admin_logged_in"] = True
            session.permanent = True
            dest = request.form.get("next") or url_for("index")
            return redirect(dest)
        flash("Usuario o contraseña incorrectos.", "error")
    return render_template("login.html", next=request.args.get("next", ""))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

ALLOWED = {"png", "jpg", "jpeg", "webp", "gif"}
ALLOWED_LOGO = ALLOWED | {"svg"}

DEFAULT_CATEGORIES = ["Tops", "Shorts", "Camisas", "Conjuntos", "Licras"]
DEFAULT_SETTINGS = {
    "brand_name": "sportswear_ST",
    "logo": "",
    "primary_color": "#97a2ff",
    "accent_color": "#2d42ff",
    "tagline": "Ropa deportiva para tu estilo.",
    "short_description": "Catálogo oficial de sportswear_ST: ropa deportiva moderna para tu día a día.",
    "instagram_url": "",
    "whatsapp_number": "",
    "facebook_url": "https://www.facebook.com/Sportswearst1",
    # Banner grande / hero de la landing (/inicio)
    "banner_image": "",
    "banner_title": "NUEVA COLECCIÓN",
    "banner_subtitle": "Ropa deportiva para tu estilo.",
    "banner_cta_text": "Ver catálogo",
    "hero_photo": "",  # foto del modelo, idealmente sin fondo (PNG transparente)
    # Barra de anuncio (encima del header) y frase grande tipo "statement"
    "announcement_text": "Escríbenos por WhatsApp y te ayudamos a elegir tu talla.",
    "statement_text": "",
    # Garantías / envíos / ubicación (sección de confianza en la landing)
    "location_text": "Ubicados en Cúcuta, Norte de Santander. Enviamos a toda Colombia y Venezuela.",
    "shipping_text": "Colombia: Interrapidísimo. Venezuela: Tealca, MRW y Zoom.",
    "warranty_text": "Cambios por defectos de fabricación o por talla equivocada, hasta 3 días después de recibida la prenda. No se hacen devoluciones de dinero, solo cambios o conversión a otra prenda.",
    # Promoción destacada (franja oscura en la landing)
    "promo_title": "Promoción activa",
    "promo_text": "Escríbenos por WhatsApp para conocer las promociones y combos del mes.",
    "promo_cta_text": "Ver promoción",
    "promo_image": "",
    # Catálogo mayorista (/mayoristas)
    "wholesale_min_qty": "6",
    "wholesale_note": "",
    # Google Analytics (GA4) — opcional, se activa solo si se llena el Measurement ID
    "ga_measurement_id": "",
}


# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------
def db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 8000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass
    return conn


def _column_names(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn, table, col, decl):
    if col not in _column_names(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def init_db():
    """Crea las tablas si no existen y migra las existentes de forma aditiva.
    Nunca borra tablas ni filas existentes."""
    conn = db()

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reference TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            cost REAL NOT NULL DEFAULT 0,
            sale_price REAL NOT NULL DEFAULT 0,
            sizes TEXT DEFAULT '',
            image TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS colors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            color TEXT NOT NULL,
            image TEXT,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reference TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            sale_price REAL NOT NULL DEFAULT 0,
            image TEXT
        );
        CREATE TABLE IF NOT EXISTS set_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            set_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(set_id) REFERENCES sets(id) ON DELETE CASCADE,
            FOREIGN KEY(product_id) REFERENCES products(id)
        );
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            sort_order INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS set_colors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            set_id INTEGER NOT NULL,
            color TEXT NOT NULL,
            image TEXT,
            FOREIGN KEY(set_id) REFERENCES sets(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_date TEXT NOT NULL,
            product_ref TEXT DEFAULT '',
            product_name TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            cost REAL NOT NULL DEFAULT 0,
            sale_price REAL NOT NULL DEFAULT 0,
            shipping_cost REAL NOT NULL DEFAULT 0,
            shipping_payer TEXT NOT NULL DEFAULT 'ninguno',
            customer_name TEXT DEFAULT '',
            customer_phone TEXT DEFAULT '',
            customer_location TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS size_guide_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            talla TEXT NOT NULL,
            pecho TEXT DEFAULT '',
            cintura TEXT DEFAULT '',
            cadera TEXT DEFAULT '',
            largo TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'pendiente',
            channel TEXT NOT NULL DEFAULT 'catalogo',
            customer_name TEXT DEFAULT '',
            customer_phone TEXT DEFAULT '',
            items_json TEXT NOT NULL DEFAULT '[]',
            total REAL NOT NULL DEFAULT 0,
            confirmed_at TEXT
        );
        """
    )

    # Columnas nuevas en tablas ya existentes (no se pierde nada)
    _add_column(conn, "products", "description", "TEXT DEFAULT ''")
    _add_column(conn, "products", "updated_at", "TEXT")
    _add_column(conn, "products", "published", "INTEGER NOT NULL DEFAULT 1")
    _add_column(conn, "products", "wholesale_price", "REAL NOT NULL DEFAULT 0")
    _add_column(conn, "sets", "description", "TEXT DEFAULT ''")
    _add_column(conn, "sets", "updated_at", "TEXT")
    _add_column(conn, "sets", "published", "INTEGER NOT NULL DEFAULT 1")
    _add_column(conn, "sets", "sizes", "TEXT DEFAULT ''")

    conn.execute("UPDATE products SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP) WHERE updated_at IS NULL")
    conn.execute("UPDATE sets SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL")
    conn.execute("UPDATE products SET published = 1 WHERE published IS NULL")
    conn.execute("UPDATE sets SET published = 1 WHERE published IS NULL")

    for i, cat in enumerate(DEFAULT_CATEGORIES):
        conn.execute("INSERT OR IGNORE INTO categories(name, sort_order) VALUES (?, ?)", (cat, i))

    existing_cats = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT category FROM products WHERE category IS NOT NULL AND TRIM(category) <> ''"
        )
    ]
    for cat in existing_cats:
        conn.execute("INSERT OR IGNORE INTO categories(name, sort_order) VALUES (?, 999)", (cat,))

    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))

    conn.commit()
    conn.close()


# Se ejecuta siempre al importar la app (tanto con "python app.py" en local
# como con gunicorn en producción), para que las tablas y migraciones
# existan sin depender de cómo se arranque el servidor.
init_db()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_settings(conn):
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    s = dict(DEFAULT_SETTINGS)
    s.update({r["key"]: r["value"] for r in rows})
    return s


def get_categories(conn, exclude_conjuntos=False):
    q = "SELECT id, name, sort_order FROM categories"
    if exclude_conjuntos:
        q += " WHERE name <> 'Conjuntos'"
    q += " ORDER BY sort_order, name"
    return [dict(r) for r in conn.execute(q).fetchall()]


def distinct_colors(conn):
    rows = conn.execute("SELECT DISTINCT color FROM colors WHERE color IS NOT NULL AND TRIM(color) <> '' ORDER BY color").fetchall()
    return [r["color"] for r in rows]


def get_size_guide(conn):
    """Filas de la tabla de tallas (medidas estándar), en el orden en que
    se agregaron. Se muestran en el catálogo público solo si hay al menos
    una fila — mientras esté vacía, el botón "Tabla de tallas" avisa que
    todavía no se ha cargado desde el panel."""
    rows = conn.execute(
        "SELECT id, talla, pecho, cintura, cadera, largo FROM size_guide_rows ORDER BY sort_order, id"
    ).fetchall()
    return [dict(r) for r in rows]


def distinct_sizes(conn):
    rows = conn.execute("SELECT sizes FROM products WHERE sizes IS NOT NULL AND TRIM(sizes) <> ''").fetchall()
    out = set()
    for r in rows:
        for token in (r["sizes"] or "").split(","):
            token = token.strip()
            if token:
                out.add(token)
    return sorted(out)


def digits_only(s):
    return "".join(ch for ch in (s or "") if ch.isdigit())


COLOR_HEX_MAP = {
    "negro": "#111111", "black": "#111111", "negro mate": "#1a1a1a",
    "blanco": "#ffffff", "white": "#ffffff", "hueso": "#f2ede1", "marfil": "#f2ede1",
    "rosado": "#f472b6", "rosa": "#f472b6", "pink": "#f472b6", "fucsia": "#e0249a",
    "azul": "#2d42ff", "blue": "#2d42ff", "celeste": "#7dd3fc", "azul cielo": "#7dd3fc",
    "azul rey": "#1e3a8a", "navy": "#1e3a8a", "azul marino": "#1e3a8a", "azul petroleo": "#0f4c5c", "azul petróleo": "#0f4c5c",
    "rojo": "#ef4444", "red": "#ef4444", "coral": "#ff6f61", "salmon": "#fa8072", "salmón": "#fa8072",
    "verde": "#22c55e", "green": "#22c55e", "verde militar": "#4b5320", "verde oliva": "#6b7a3a", "oliva": "#6b7a3a",
    "verde menta": "#7fd8be", "menta": "#7fd8be", "verde limon": "#a3e635", "verde limón": "#a3e635",
    "gris": "#9ca3af", "grey": "#9ca3af", "gray": "#9ca3af", "gris claro": "#d1d5db", "gris oscuro": "#4b5563",
    "gris jaspe": "#a8a8a8", "gris jaspeado": "#a8a8a8", "plomo": "#6b7280",
    "amarillo": "#eab308", "yellow": "#eab308", "mostaza": "#c9971b",
    "naranja": "#f97316", "orange": "#f97316",
    "morado": "#a855f7", "purple": "#a855f7", "violeta": "#a855f7", "lila": "#c4b5fd", "lavanda": "#b9a5e8",
    "beige": "#e7d9c4", "crema": "#f5ecd9", "arena": "#d9c6a5", "kaki": "#c3b091", "caqui": "#c3b091",
    "café": "#7c4a2d", "cafe": "#7c4a2d", "marron": "#7c4a2d", "marrón": "#7c4a2d", "vinotinto": "#7f1d3a", "vino": "#7f1d3a",
    "turquesa": "#14b8a6", "aqua": "#22d3d8", "dorado": "#d4af37", "plateado": "#c0c0c0", "perla": "#e7e5e0",
}


def color_hex(name):
    return COLOR_HEX_MAP.get((name or "").strip().lower(), "#97a2ff")


# Lado más largo al que se reduce cualquier foto subida (sobra para verse
# nítida en pantalla, incluso en retina, y pesa mucho menos que la foto
# original de un celular moderno, que suele venir en 3000-4000px).
MAX_IMAGE_SIDE = 1600
JPEG_QUALITY = 82


def save_image(file, allowed=ALLOWED):
    if not file or not file.filename:
        return None
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in allowed:
        return None
    name = f"{uuid.uuid4().hex}.{ext}"
    dest = UPLOADS / name
    # SVG (logos) y GIF (podrían ser animados) se guardan tal cual, sin pasar
    # por Pillow. El resto de formatos se comprime: se reduce el tamaño si la
    # foto es más grande de lo necesario y se vuelve a guardar optimizada.
    if Image is None or ext in {"svg", "gif"}:
        file.save(dest)
        return name
    try:
        img = Image.open(file.stream)
        img = ImageOps.exif_transpose(img)  # respeta la orientación de fotos de celular
        img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.LANCZOS)
        if ext in {"jpg", "jpeg"}:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(dest, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        elif ext == "png":
            img.save(dest, format="PNG", optimize=True)
        elif ext == "webp":
            img.save(dest, format="WEBP", quality=JPEG_QUALITY)
        else:
            img.save(dest)
    except Exception:
        # Si la imagen viene corrupta o Pillow no puede procesarla, igual la
        # guardamos tal cual llegó en vez de perder la subida del usuario.
        file.stream.seek(0)
        file.save(dest)
    return name


def normalize_sizes(raw):
    return ", ".join(sorted({s.strip() for s in (raw or "").split(",") if s.strip()}, key=lambda x: x))


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


ORDER_STATUS_LABELS = {"pendiente": "Pendiente", "confirmado": "Confirmado", "cancelado": "Cancelado"}


def new_order_slug():
    return uuid.uuid4().hex[:10]


def product_cost_lookup(conn):
    """Mapa referencia -> costo unitario, para productos y conjuntos, usado al
    confirmar un pedido web y pasarlo a Ventas con el costo real de hoy."""
    out = {}
    for p in conn.execute("SELECT reference, cost FROM products"):
        out[p["reference"]] = p["cost"] or 0
    for s in _sets_with_cost(conn):
        out[s["reference"]] = s["cost"] or 0
    return out


@app.template_filter("money")
def money(v):
    try:
        return "${:,.0f}".format(float(v or 0))
    except (ValueError, TypeError):
        return "$0"


@app.template_filter("wanumber")
def wanumber(v):
    return digits_only(v)


@app.template_filter("colorhex")
def colorhex_filter(v):
    return color_hex(v)


@app.context_processor
def inject_globals():
    conn = db()
    brand = get_settings(conn)
    conn.close()
    return {"brand": brand}


# ---------------------------------------------------------------------------
# Archivos subidos (imágenes / logo)
# ---------------------------------------------------------------------------
@app.get("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOADS, filename)


# ---------------------------------------------------------------------------
# Dashboard / Inventario
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    conn = db()
    total_products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    total_sets = conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0]
    total_categories = conn.execute("SELECT COUNT(*) FROM categories WHERE name <> 'Conjuntos'").fetchone()[0]
    recent_products = [dict(r) for r in conn.execute(
        "SELECT * FROM products ORDER BY datetime(updated_at) DESC, id DESC LIMIT 6"
    ).fetchall()]
    recent_sets = [dict(r) for r in conn.execute(
        """SELECT s.*, COALESCE(SUM(p.cost * si.quantity),0) AS cost
           FROM sets s
           LEFT JOIN set_items si ON si.set_id=s.id
           LEFT JOIN products p ON p.id=si.product_id
           GROUP BY s.id ORDER BY s.id DESC LIMIT 6"""
    ).fetchall()]
    conn.close()
    return render_template(
        "dashboard.html",
        total_products=total_products,
        total_sets=total_sets,
        total_categories=total_categories,
        recent_products=recent_products,
        recent_sets=recent_sets,
    )


# ---------------------------------------------------------------------------
# Productos
# ---------------------------------------------------------------------------
def _products_with_colors(conn):
    rows = conn.execute(
        """SELECT p.*, GROUP_CONCAT(DISTINCT c.color) AS colors_list
           FROM products p LEFT JOIN colors c ON c.product_id = p.id
           GROUP BY p.id ORDER BY p.category, p.reference"""
    ).fetchall()
    return [dict(r) for r in rows]


@app.route("/productos")
def products_list():
    conn = db()
    products = _products_with_colors(conn)
    categories = get_categories(conn, exclude_conjuntos=True)
    colors = distinct_colors(conn)
    sizes = distinct_sizes(conn)
    conn.close()
    return render_template(
        "products_list.html",
        products=products,
        categories=categories,
        colors=colors,
        sizes=sizes,
    )


@app.route("/productos/nuevo", methods=["GET", "POST"])
def product_new():
    conn = db()
    try:
        if request.method == "POST":
            reference = request.form["reference"].strip().upper()
            category = request.form["category"]
            name = request.form["name"].strip()
            description = request.form.get("description", "").strip()
            cost = float(request.form.get("cost") or 0)
            sale = float(request.form.get("sale_price") or 0)
            wholesale = float(request.form.get("wholesale_price") or 0)
            sizes = normalize_sizes(request.form.get("sizes", ""))
            published = 1 if request.form.get("published") else 0
            image = save_image(request.files.get("image"))
            try:
                cur = conn.execute(
                    """INSERT INTO products(reference,category,name,description,cost,sale_price,wholesale_price,sizes,image,created_at,updated_at,published)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (reference, category, name, description, cost, sale, wholesale, sizes, image, now_str(), now_str(), published),
                )
                product_id = cur.lastrowid
                colors = request.form.getlist("color")
                color_images = request.files.getlist("color_image")
                for i, color in enumerate(colors):
                    if color.strip():
                        ci = save_image(color_images[i]) if i < len(color_images) else None
                        conn.execute(
                            "INSERT INTO colors(product_id,color,image) VALUES(?,?,?)",
                            (product_id, color.strip(), ci),
                        )
                conn.commit()
                flash("Producto guardado correctamente.", "ok")
                return redirect(url_for("products_list"))
            except sqlite3.IntegrityError:
                conn.rollback()
                flash("La referencia ya existe. Usa una referencia diferente.", "error")

        categories = get_categories(conn, exclude_conjuntos=True)
        return render_template("product_form.html", product=None, colors=[], categories=categories, mode="nuevo")
    finally:
        conn.close()


@app.route("/productos/<int:pid>")
def product_detail(pid):
    conn = db()
    product = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not product:
        conn.close()
        flash("El producto no existe.", "error")
        return redirect(url_for("products_list"))
    colors = [dict(r) for r in conn.execute("SELECT * FROM colors WHERE product_id=? ORDER BY id", (pid,)).fetchall()]
    conn.close()
    return render_template("product_detail.html", product=dict(product), colors=colors)


@app.route("/productos/<int:pid>/editar", methods=["GET", "POST"])
def product_edit(pid):
    conn = db()
    try:
        product = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        if not product:
            flash("El producto no existe.", "error")
            return redirect(url_for("products_list"))

        if request.method == "POST":
            reference = request.form["reference"].strip().upper()
            category = request.form["category"]
            name = request.form["name"].strip()
            description = request.form.get("description", "").strip()
            cost = float(request.form.get("cost") or 0)
            sale = float(request.form.get("sale_price") or 0)
            wholesale = float(request.form.get("wholesale_price") or 0)
            sizes = normalize_sizes(request.form.get("sizes", ""))
            published = 1 if request.form.get("published") else 0

            new_image = save_image(request.files.get("image"))
            image_value = new_image if new_image else product["image"]

            try:
                conn.execute(
                    """UPDATE products SET reference=?, category=?, name=?, description=?, cost=?,
                       sale_price=?, wholesale_price=?, sizes=?, image=?, updated_at=?, published=? WHERE id=?""",
                    (reference, category, name, description, cost, sale, wholesale, sizes, image_value, now_str(), published, pid),
                )

                # Colores existentes: actualizar, reemplazar imagen si se sube una nueva, o eliminar
                color_ids = request.form.getlist("color_id")
                color_names = request.form.getlist("color_name")
                color_images = request.files.getlist("color_image_existing")
                color_deletes = set(request.form.getlist("color_delete"))
                for i, cid in enumerate(color_ids):
                    if cid in color_deletes:
                        conn.execute("DELETE FROM colors WHERE id=? AND product_id=?", (cid, pid))
                        continue
                    cname = color_names[i].strip() if i < len(color_names) else ""
                    if not cname:
                        continue
                    new_ci = save_image(color_images[i]) if i < len(color_images) else None
                    if new_ci:
                        conn.execute("UPDATE colors SET color=?, image=? WHERE id=? AND product_id=?", (cname, new_ci, cid, pid))
                    else:
                        conn.execute("UPDATE colors SET color=? WHERE id=? AND product_id=?", (cname, cid, pid))

                # Colores nuevos
                new_color_names = request.form.getlist("new_color")
                new_color_images = request.files.getlist("new_color_image")
                for i, cname in enumerate(new_color_names):
                    if cname.strip():
                        ci = save_image(new_color_images[i]) if i < len(new_color_images) else None
                        conn.execute(
                            "INSERT INTO colors(product_id,color,image) VALUES(?,?,?)",
                            (pid, cname.strip(), ci),
                        )

                conn.commit()
                flash("Producto actualizado correctamente.", "ok")
                return redirect(url_for("product_detail", pid=pid))
            except sqlite3.IntegrityError:
                conn.rollback()
                flash("La referencia ya existe en otro producto.", "error")

        colors = [dict(r) for r in conn.execute("SELECT * FROM colors WHERE product_id=? ORDER BY id", (pid,)).fetchall()]
        categories = get_categories(conn, exclude_conjuntos=True)
        return render_template("product_form.html", product=dict(product), colors=colors, categories=categories, mode="editar")
    finally:
        conn.close()


@app.post("/productos/<int:pid>/eliminar")
def product_delete(pid):
    conn = db()
    try:
        used_in = conn.execute(
            """SELECT DISTINCT s.reference FROM set_items si
               JOIN sets s ON s.id = si.set_id WHERE si.product_id=?""",
            (pid,),
        ).fetchall()
        if used_in:
            refs = ", ".join(r["reference"] for r in used_in)
            flash(
                f"No se puede eliminar: esta referencia hace parte del/de los conjunto(s) {refs}. "
                "Quítala de esos conjuntos primero.",
                "error",
            )
        else:
            conn.execute("DELETE FROM products WHERE id=?", (pid,))
            conn.commit()
            flash("Producto eliminado.", "ok")
    except sqlite3.IntegrityError:
        conn.rollback()
        flash("No se pudo eliminar el producto porque tiene información relacionada.", "error")
    finally:
        conn.close()
    return redirect(url_for("products_list"))


@app.post("/productos/<int:pid>/publicar-toggle")
def product_toggle_published(pid):
    conn = db()
    try:
        row = conn.execute("SELECT published FROM products WHERE id=?", (pid,)).fetchone()
        if row is not None:
            new_val = 0 if row["published"] else 1
            conn.execute("UPDATE products SET published=? WHERE id=?", (new_val, pid))
            conn.commit()
            flash("Producto publicado en el catálogo." if new_val else "Producto oculto del catálogo.", "ok")
    finally:
        conn.close()
    return redirect(request.referrer or url_for("products_list"))


# ---------------------------------------------------------------------------
# Conjuntos
# ---------------------------------------------------------------------------
def _sets_with_cost(conn):
    rows = conn.execute(
        """SELECT s.*, COALESCE(SUM(p.cost * si.quantity),0) AS cost
           FROM sets s
           LEFT JOIN set_items si ON si.set_id=s.id
           LEFT JOIN products p ON p.id=si.product_id
           GROUP BY s.id ORDER BY s.reference"""
    ).fetchall()
    return [dict(r) for r in rows]


def _set_items_detail(conn, sid):
    rows = conn.execute(
        """SELECT si.id AS item_id, si.quantity, p.id AS product_id, p.reference, p.name, p.cost, p.sale_price, p.image
           FROM set_items si JOIN products p ON p.id = si.product_id
           WHERE si.set_id=? ORDER BY p.reference""",
        (sid,),
    ).fetchall()
    return [dict(r) for r in rows]


def _set_colors(conn, sid):
    rows = conn.execute("SELECT * FROM set_colors WHERE set_id=? ORDER BY id", (sid,)).fetchall()
    return [dict(r) for r in rows]


@app.route("/conjuntos")
def sets_list():
    conn = db()
    sets_ = _sets_with_cost(conn)
    conn.close()
    return render_template("sets_list.html", sets=sets_)


@app.route("/conjuntos/nuevo", methods=["GET", "POST"])
def set_new():
    conn = db()
    try:
        if request.method == "POST":
            reference = request.form["set_reference"].strip().upper()
            name = request.form["set_name"].strip()
            description = request.form.get("set_description", "").strip()
            sale = float(request.form.get("set_sale_price") or 0)
            published = 1 if request.form.get("published") else 0
            image = save_image(request.files.get("set_image"))
            product_ids = request.form.getlist("set_product_id")
            quantities = request.form.getlist("set_quantity")
            try:
                cur = conn.execute(
                    """INSERT INTO sets(reference,name,description,sale_price,image,updated_at,sizes,published)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (reference, name, description, sale, image, now_str(), "", published),
                )
                sid = cur.lastrowid
                for i, pid in enumerate(product_ids):
                    if pid:
                        qty = int(quantities[i] or 1) if i < len(quantities) else 1
                        conn.execute(
                            "INSERT INTO set_items(set_id,product_id,quantity) VALUES(?,?,?)",
                            (sid, int(pid), max(1, qty)),
                        )
                colors = request.form.getlist("color")
                color_images = request.files.getlist("color_image")
                for i, color in enumerate(colors):
                    if color.strip():
                        ci = save_image(color_images[i]) if i < len(color_images) else None
                        conn.execute(
                            "INSERT INTO set_colors(set_id,color,image) VALUES(?,?,?)",
                            (sid, color.strip(), ci),
                        )
                conn.commit()
                flash("Conjunto guardado correctamente.", "ok")
                return redirect(url_for("sets_list"))
            except sqlite3.IntegrityError:
                conn.rollback()
                flash("La referencia del conjunto ya existe.", "error")

        products = _products_with_colors(conn)
        return render_template("set_form.html", set=None, items=[], colors=[], products=products, mode="nuevo")
    finally:
        conn.close()


@app.route("/conjuntos/<int:sid>")
def set_detail(sid):
    conn = db()
    s = conn.execute(
        """SELECT s.*, COALESCE(SUM(p.cost * si.quantity),0) AS cost
           FROM sets s
           LEFT JOIN set_items si ON si.set_id=s.id
           LEFT JOIN products p ON p.id=si.product_id
           WHERE s.id=? GROUP BY s.id""",
        (sid,),
    ).fetchone()
    if not s:
        conn.close()
        flash("El conjunto no existe.", "error")
        return redirect(url_for("sets_list"))
    items = _set_items_detail(conn, sid)
    colors = _set_colors(conn, sid)
    conn.close()
    return render_template("set_detail.html", s=dict(s), items=items, colors=colors)


@app.route("/conjuntos/<int:sid>/editar", methods=["GET", "POST"])
def set_edit(sid):
    conn = db()
    try:
        s = conn.execute("SELECT * FROM sets WHERE id=?", (sid,)).fetchone()
        if not s:
            flash("El conjunto no existe.", "error")
            return redirect(url_for("sets_list"))

        if request.method == "POST":
            reference = request.form["set_reference"].strip().upper()
            name = request.form["set_name"].strip()
            description = request.form.get("set_description", "").strip()
            sale = float(request.form.get("set_sale_price") or 0)
            published = 1 if request.form.get("published") else 0
            new_image = save_image(request.files.get("set_image"))
            image_value = new_image if new_image else s["image"]
            product_ids = request.form.getlist("set_product_id")
            quantities = request.form.getlist("set_quantity")
            try:
                conn.execute(
                    """UPDATE sets SET reference=?, name=?, description=?, sale_price=?, image=?,
                       updated_at=?, sizes=?, published=? WHERE id=?""",
                    (reference, name, description, sale, image_value, now_str(), "", published, sid),
                )
                conn.execute("DELETE FROM set_items WHERE set_id=?", (sid,))
                for i, pid in enumerate(product_ids):
                    if pid:
                        qty = int(quantities[i] or 1) if i < len(quantities) else 1
                        conn.execute(
                            "INSERT INTO set_items(set_id,product_id,quantity) VALUES(?,?,?)",
                            (sid, int(pid), max(1, qty)),
                        )

                # Colores existentes del conjunto: actualizar, reemplazar imagen o eliminar
                color_ids = request.form.getlist("color_id")
                color_names = request.form.getlist("color_name")
                color_images = request.files.getlist("color_image_existing")
                color_deletes = set(request.form.getlist("color_delete"))
                for i, cid in enumerate(color_ids):
                    if cid in color_deletes:
                        conn.execute("DELETE FROM set_colors WHERE id=? AND set_id=?", (cid, sid))
                        continue
                    cname = color_names[i].strip() if i < len(color_names) else ""
                    if not cname:
                        continue
                    new_ci = save_image(color_images[i]) if i < len(color_images) else None
                    if new_ci:
                        conn.execute("UPDATE set_colors SET color=?, image=? WHERE id=? AND set_id=?", (cname, new_ci, cid, sid))
                    else:
                        conn.execute("UPDATE set_colors SET color=? WHERE id=? AND set_id=?", (cname, cid, sid))

                # Colores nuevos del conjunto
                new_color_names = request.form.getlist("new_color")
                new_color_images = request.files.getlist("new_color_image")
                for i, cname in enumerate(new_color_names):
                    if cname.strip():
                        ci = save_image(new_color_images[i]) if i < len(new_color_images) else None
                        conn.execute(
                            "INSERT INTO set_colors(set_id,color,image) VALUES(?,?,?)",
                            (sid, cname.strip(), ci),
                        )

                conn.commit()
                flash("Conjunto actualizado correctamente.", "ok")
                return redirect(url_for("set_detail", sid=sid))
            except sqlite3.IntegrityError:
                conn.rollback()
                flash("La referencia del conjunto ya existe en otro conjunto.", "error")

        items = _set_items_detail(conn, sid)
        colors = _set_colors(conn, sid)
        products = _products_with_colors(conn)
        return render_template("set_form.html", set=dict(s), items=items, colors=colors, products=products, mode="editar")
    finally:
        conn.close()


@app.post("/conjuntos/<int:sid>/eliminar")
def set_delete(sid):
    conn = db()
    try:
        conn.execute("DELETE FROM sets WHERE id=?", (sid,))
        conn.commit()
        flash("Conjunto eliminado.", "ok")
    except sqlite3.IntegrityError:
        conn.rollback()
        flash("No se pudo eliminar el conjunto.", "error")
    finally:
        conn.close()
    return redirect(url_for("sets_list"))


@app.post("/conjuntos/<int:sid>/publicar-toggle")
def set_toggle_published(sid):
    conn = db()
    try:
        row = conn.execute("SELECT published FROM sets WHERE id=?", (sid,)).fetchone()
        if row is not None:
            new_val = 0 if row["published"] else 1
            conn.execute("UPDATE sets SET published=? WHERE id=?", (new_val, sid))
            conn.commit()
            flash("Conjunto publicado en el catálogo." if new_val else "Conjunto oculto del catálogo.", "ok")
    finally:
        conn.close()
    return redirect(request.referrer or url_for("sets_list"))


# ---------------------------------------------------------------------------
# Exportar (CSV con costo / venta / ganancia, para llevarlo a Excel)
# ---------------------------------------------------------------------------
@app.route("/exportar/catalogo.csv")
def export_catalog_csv():
    conn = db()
    products = _products_with_colors(conn)
    sets_ = _sets_with_cost(conn)
    conn.close()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Tipo", "Referencia", "Nombre", "Categoría", "Colores", "Tallas",
        "Costo", "Precio de venta", "Ganancia", "Margen %", "Publicado",
    ])

    def add_row(tipo, reference, name, category, colors_list, sizes, cost, sale_price, published):
        cost = float(cost or 0)
        sale_price = float(sale_price or 0)
        profit = sale_price - cost
        margin = round((profit / sale_price) * 100, 1) if sale_price else 0
        writer.writerow([
            tipo, reference, name, category or "", colors_list or "", sizes or "",
            round(cost), round(sale_price), round(profit),
            margin, "Sí" if published else "No",
        ])

    for p in products:
        add_row("Producto", p["reference"], p["name"], p.get("category"), p.get("colors_list"), p.get("sizes"),
                p.get("cost"), p.get("sale_price"), p.get("published"))

    for s in sets_:
        add_row("Conjunto", s["reference"], s["name"], "Conjuntos", "", s.get("sizes"),
                s.get("cost"), s.get("sale_price"), s.get("published"))

    csv_bytes = "﻿" + buf.getvalue()  # BOM para que Excel muestre bien las tildes/ñ
    filename = "catalogo_costo_venta_" + now_str().replace(" ", "_").replace(":", "-") + ".csv"
    return Response(
        csv_bytes,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Registro de ventas (solo visible aquí, en el administrador — nunca en el
# catálogo público. No modifica productos/conjuntos ni su inventario.)
# ---------------------------------------------------------------------------
SHIPPING_PAYER_LABELS = {
    "ninguno": "Sin envío",
    "tienda": "Yo pago el envío",
    "cliente": "El cliente paga el envío",
}


def _sale_profit(cost, sale_price, shipping_cost, shipping_payer):
    ship = float(shipping_cost or 0) if shipping_payer == "tienda" else 0.0
    return float(sale_price or 0) - float(cost or 0) - ship


def _sales_catalog_options(conn):
    """Lista de productos y conjuntos para autocompletar la venta (solo lectura)."""
    options = []
    for p in conn.execute("SELECT reference, name, cost, sale_price FROM products ORDER BY reference"):
        options.append({"ref": p["reference"], "name": p["name"], "cost": p["cost"], "sale_price": p["sale_price"]})
    for s in _sets_with_cost(conn):
        options.append({"ref": s["reference"], "name": s["name"], "cost": s["cost"], "sale_price": s["sale_price"]})
    return options


def _sale_form_values(form):
    return {
        "sale_date": form.get("sale_date") or now_str()[:10],
        "product_ref": form.get("product_ref", "").strip(),
        "product_name": form.get("product_name", "").strip(),
        "quantity": int(form.get("quantity") or 1),
        "cost": float(form.get("cost") or 0),
        "sale_price": float(form.get("sale_price") or 0),
        "shipping_cost": float(form.get("shipping_cost") or 0),
        "shipping_payer": form.get("shipping_payer") or "ninguno",
        "customer_name": form.get("customer_name", "").strip(),
        "customer_phone": form.get("customer_phone", "").strip(),
        "customer_location": form.get("customer_location", "").strip(),
        "notes": form.get("notes", "").strip(),
    }


@app.route("/ventas")
def sales_list():
    conn = db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM sales ORDER BY date(sale_date) DESC, id DESC").fetchall()]
    conn.close()
    for r in rows:
        r["profit"] = _sale_profit(r["cost"], r["sale_price"], r["shipping_cost"], r["shipping_payer"])
        r["payer_label"] = SHIPPING_PAYER_LABELS.get(r["shipping_payer"], r["shipping_payer"])
    return render_template(
        "sales_list.html",
        sales=rows,
        total_sales=len(rows),
        total_revenue=sum(r["sale_price"] or 0 for r in rows),
        total_cost=sum(r["cost"] or 0 for r in rows),
        total_profit=sum(r["profit"] for r in rows),
    )


@app.route("/ventas/nueva", methods=["GET", "POST"])
def sale_new():
    conn = db()
    try:
        if request.method == "POST":
            v = _sale_form_values(request.form)
            if not v["product_name"]:
                flash("Escribe el nombre del producto vendido.", "error")
            else:
                conn.execute(
                    """INSERT INTO sales(sale_date,product_ref,product_name,quantity,cost,sale_price,
                       shipping_cost,shipping_payer,customer_name,customer_phone,customer_location,notes,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (v["sale_date"], v["product_ref"], v["product_name"], v["quantity"], v["cost"], v["sale_price"],
                     v["shipping_cost"], v["shipping_payer"], v["customer_name"], v["customer_phone"],
                     v["customer_location"], v["notes"], now_str()),
                )
                conn.commit()
                flash("Venta registrada correctamente.", "ok")
                return redirect(url_for("sales_list"))

        return render_template(
            "sale_form.html", sale=None, mode="nueva",
            catalog_options=_sales_catalog_options(conn), today=now_str()[:10],
            payer_labels=SHIPPING_PAYER_LABELS,
        )
    finally:
        conn.close()


@app.route("/ventas/<int:said>/editar", methods=["GET", "POST"])
def sale_edit(said):
    conn = db()
    try:
        sale = conn.execute("SELECT * FROM sales WHERE id=?", (said,)).fetchone()
        if not sale:
            flash("La venta no existe.", "error")
            return redirect(url_for("sales_list"))

        if request.method == "POST":
            v = _sale_form_values(request.form)
            if not v["product_name"]:
                flash("Escribe el nombre del producto vendido.", "error")
            else:
                conn.execute(
                    """UPDATE sales SET sale_date=?, product_ref=?, product_name=?, quantity=?, cost=?, sale_price=?,
                       shipping_cost=?, shipping_payer=?, customer_name=?, customer_phone=?, customer_location=?, notes=?
                       WHERE id=?""",
                    (v["sale_date"], v["product_ref"], v["product_name"], v["quantity"], v["cost"], v["sale_price"],
                     v["shipping_cost"], v["shipping_payer"], v["customer_name"], v["customer_phone"],
                     v["customer_location"], v["notes"], said),
                )
                conn.commit()
                flash("Venta actualizada correctamente.", "ok")
                return redirect(url_for("sales_list"))

        return render_template(
            "sale_form.html", sale=dict(sale), mode="editar",
            catalog_options=_sales_catalog_options(conn), today=now_str()[:10],
            payer_labels=SHIPPING_PAYER_LABELS,
        )
    finally:
        conn.close()


@app.route("/ventas/<int:said>/eliminar", methods=["POST"])
def sale_delete(said):
    conn = db()
    try:
        conn.execute("DELETE FROM sales WHERE id=?", (said,))
        conn.commit()
        flash("Venta eliminada.", "ok")
    finally:
        conn.close()
    return redirect(url_for("sales_list"))


@app.route("/ventas/exportar.csv")
def export_sales_csv():
    conn = db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM sales ORDER BY date(sale_date) DESC, id DESC").fetchall()]
    conn.close()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Fecha", "Referencia", "Producto", "Cantidad", "Costo", "Precio de venta", "Envío",
        "Quién paga el envío", "Ganancia neta", "Cliente", "Teléfono", "Ciudad/lugar", "Notas",
    ])
    for r in rows:
        profit = _sale_profit(r["cost"], r["sale_price"], r["shipping_cost"], r["shipping_payer"])
        writer.writerow([
            r["sale_date"], r["product_ref"], r["product_name"], r["quantity"],
            round(r["cost"]), round(r["sale_price"]), round(r["shipping_cost"]),
            SHIPPING_PAYER_LABELS.get(r["shipping_payer"], r["shipping_payer"]),
            round(profit), r["customer_name"], r["customer_phone"], r["customer_location"], r["notes"],
        ])

    csv_bytes = "﻿" + buf.getvalue()
    filename = "ventas_" + now_str().replace(" ", "_").replace(":", "-") + ".csv"
    return Response(
        csv_bytes,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Categorías
# ---------------------------------------------------------------------------
@app.route("/categorias")
def categories_page():
    conn = db()
    categories = conn.execute(
        """SELECT c.id, c.name, c.sort_order,
                  (SELECT COUNT(*) FROM products p WHERE p.category = c.name) AS product_count
           FROM categories c ORDER BY c.sort_order, c.name"""
    ).fetchall()
    conn.close()
    return render_template("categories.html", categories=[dict(r) for r in categories])


@app.post("/categorias/nueva")
def category_new():
    name = request.form.get("name", "").strip()
    conn = db()
    try:
        if not name:
            flash("Escribe un nombre de categoría.", "error")
        else:
            try:
                max_order = conn.execute("SELECT COALESCE(MAX(sort_order),0) FROM categories").fetchone()[0]
                conn.execute("INSERT INTO categories(name, sort_order) VALUES (?, ?)", (name, max_order + 1))
                conn.commit()
                flash("Categoría agregada.", "ok")
            except sqlite3.IntegrityError:
                conn.rollback()
                flash("Esa categoría ya existe.", "error")
    finally:
        conn.close()
    return redirect(url_for("categories_page"))


@app.post("/categorias/<int:cid>/eliminar")
def category_delete(cid):
    conn = db()
    try:
        row = conn.execute("SELECT name FROM categories WHERE id=?", (cid,)).fetchone()
        if row:
            in_use = conn.execute("SELECT COUNT(*) FROM products WHERE category=?", (row["name"],)).fetchone()[0]
            if in_use > 0:
                flash(f"No puedes eliminar '{row['name']}' porque tiene {in_use} producto(s) asignado(s).", "error")
            elif row["name"] == "Conjuntos":
                flash("La categoría 'Conjuntos' es reservada y no se puede eliminar.", "error")
            else:
                conn.execute("DELETE FROM categories WHERE id=?", (cid,))
                conn.commit()
                flash("Categoría eliminada.", "ok")
    finally:
        conn.close()
    return redirect(url_for("categories_page"))


# ---------------------------------------------------------------------------
# Tabla de tallas (medidas estándar) — la llena el administrador una vez y
# se muestra en el botón "Tabla de tallas" de cada producto del catálogo
# público y del catálogo mayorista.
# ---------------------------------------------------------------------------
@app.route("/tallas")
def size_guide_page():
    conn = db()
    try:
        rows = get_size_guide(conn)
    finally:
        conn.close()
    return render_template("size_guide.html", rows=rows)


@app.post("/tallas/nueva")
def size_guide_new():
    talla = request.form.get("talla", "").strip()
    conn = db()
    try:
        if not talla:
            flash("Escribe el nombre de la talla (ej. S, M, L).", "error")
        else:
            max_order = conn.execute("SELECT COALESCE(MAX(sort_order),0) FROM size_guide_rows").fetchone()[0]
            conn.execute(
                "INSERT INTO size_guide_rows(talla,pecho,cintura,cadera,largo,sort_order) VALUES (?,?,?,?,?,?)",
                (
                    talla,
                    request.form.get("pecho", "").strip(),
                    request.form.get("cintura", "").strip(),
                    request.form.get("cadera", "").strip(),
                    request.form.get("largo", "").strip(),
                    max_order + 1,
                ),
            )
            conn.commit()
            flash("Fila agregada a la tabla de tallas.", "ok")
    finally:
        conn.close()
    return redirect(url_for("size_guide_page"))


@app.post("/tallas/<int:rid>/eliminar")
def size_guide_delete(rid):
    conn = db()
    try:
        conn.execute("DELETE FROM size_guide_rows WHERE id=?", (rid,))
        conn.commit()
        flash("Fila eliminada.", "ok")
    finally:
        conn.close()
    return redirect(url_for("size_guide_page"))


# ---------------------------------------------------------------------------
# Configuración de marca
# ---------------------------------------------------------------------------
@app.route("/configuracion", methods=["GET", "POST"])
def settings_page():
    conn = db()
    try:
        if request.method == "POST":
            brand_name = request.form.get("brand_name", "").strip() or "sportswear_ST"
            conn.execute("INSERT INTO settings(key,value) VALUES('brand_name',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (brand_name,))

            primary_color = request.form.get("primary_color", "").strip()
            if primary_color:
                conn.execute("INSERT INTO settings(key,value) VALUES('primary_color',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (primary_color,))
            accent_color = request.form.get("accent_color", "").strip()
            if accent_color:
                conn.execute("INSERT INTO settings(key,value) VALUES('accent_color',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (accent_color,))

            for key in (
                "tagline", "short_description", "instagram_url", "whatsapp_number", "facebook_url",
                "banner_title", "banner_subtitle", "banner_cta_text",
                "announcement_text", "statement_text",
                "location_text", "shipping_text", "warranty_text",
                "promo_title", "promo_text", "promo_cta_text",
                "wholesale_min_qty", "wholesale_note",
                "ga_measurement_id",
            ):
                val = request.form.get(key, "").strip()
                conn.execute(
                    "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, val),
                )

            logo_file = request.files.get("logo")
            if logo_file and logo_file.filename:
                logo_name = save_image(logo_file, allowed=ALLOWED_LOGO)
                if logo_name:
                    conn.execute("INSERT INTO settings(key,value) VALUES('logo',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (logo_name,))
                else:
                    flash("El logo debe ser una imagen PNG, JPG, WEBP, GIF o SVG.", "error")

            banner_file = request.files.get("banner_image")
            if banner_file and banner_file.filename:
                banner_name = save_image(banner_file)
                if banner_name:
                    conn.execute("INSERT INTO settings(key,value) VALUES('banner_image',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (banner_name,))
                else:
                    flash("La imagen del banner debe ser PNG, JPG, WEBP o GIF.", "error")

            hero_photo_file = request.files.get("hero_photo")
            if hero_photo_file and hero_photo_file.filename:
                hero_photo_name = save_image(hero_photo_file)
                if hero_photo_name:
                    conn.execute("INSERT INTO settings(key,value) VALUES('hero_photo',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (hero_photo_name,))
                else:
                    flash("La foto del modelo debe ser PNG, JPG, WEBP o GIF.", "error")

            promo_image_file = request.files.get("promo_image")
            if promo_image_file and promo_image_file.filename:
                promo_image_name = save_image(promo_image_file)
                if promo_image_name:
                    conn.execute("INSERT INTO settings(key,value) VALUES('promo_image',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (promo_image_name,))
                else:
                    flash("La imagen de la promoción debe ser PNG, JPG, WEBP o GIF.", "error")

            conn.commit()
            flash("Configuración guardada.", "ok")
            return redirect(url_for("settings_page"))

        brand = get_settings(conn)
        return render_template("settings.html", brand=brand)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Catálogo público (solo lectura, para compartir por enlace)
# ---------------------------------------------------------------------------
def image_url(filename):
    return url_for("uploaded_file", filename=filename) if filename else None


def public_catalog_items(conn):
    """Combina productos y conjuntos PUBLICADOS en una sola lista, lista para
    mostrarse en el catálogo público y para incrustarse como JSON (el carrito
    del cliente vive en su navegador, pero siempre resuelve nombre/precio/imagen
    desde aquí, así nunca queda desactualizado)."""
    items = []

    products = conn.execute(
        "SELECT * FROM products WHERE published=1 ORDER BY category, reference"
    ).fetchall()
    for p in products:
        colors = conn.execute("SELECT * FROM colors WHERE product_id=? ORDER BY id", (p["id"],)).fetchall()
        images = []
        if p["image"]:
            images.append(image_url(p["image"]))
        for c in colors:
            if c["image"]:
                u = image_url(c["image"])
                if u not in images:
                    images.append(u)
        items.append({
            "type": "producto",
            "ref": p["reference"],
            "name": p["name"],
            "category": p["category"],
            "description": p["description"] or "",
            "price": p["sale_price"],
            "image": image_url(p["image"]),
            "images": images,
            "colors": [{"name": c["color"], "image": image_url(c["image"])} for c in colors],
            "sizes": [t.strip() for t in (p["sizes"] or "").split(",") if t.strip()],
            "updated": p["updated_at"] or p["created_at"] or "",
            "includes": [],
            "url": url_for("catalog_product", reference=p["reference"]),
        })

    sets_ = conn.execute("SELECT * FROM sets WHERE published=1 ORDER BY reference").fetchall()
    for s in sets_:
        colors = _set_colors(conn, s["id"])
        set_items = _set_items_detail(conn, s["id"])
        images = []
        if s["image"]:
            images.append(image_url(s["image"]))
        for c in colors:
            if c["image"]:
                u = image_url(c["image"])
                if u not in images:
                    images.append(u)
        # Cuánto costarían las prendas si el cliente las comprara por separado,
        # para mostrarle el ahorro real de llevarlas en conjunto.
        individual_price = sum((it["sale_price"] or 0) * (it["quantity"] or 1) for it in set_items)
        savings = individual_price - (s["sale_price"] or 0)
        items.append({
            "type": "conjunto",
            "ref": s["reference"],
            "name": s["name"],
            "category": "Conjuntos",
            "description": s["description"] or "",
            "price": s["sale_price"],
            "individual_price": individual_price,
            "savings": savings,
            "image": image_url(s["image"]),
            "images": images,
            "colors": [{"name": c["color"], "image": image_url(c["image"])} for c in colors],
            # Los conjuntos no piden talla propia: cada prenda que los compone ya
            # tiene su talla (visible en "Incluye"), así que aquí siempre va vacío.
            "sizes": [],
            "updated": s["updated_at"] or "",
            "includes": [{"ref": it["reference"], "name": it["name"], "sale_price": it["sale_price"], "quantity": it["quantity"]} for it in set_items],
            "url": url_for("catalog_product", reference=s["reference"]),
        })
    return items


def landing_categories(conn, items, limit=6):
    """Una tarjeta por categoría (estilo Novashion): cuántos productos tiene
    y una foto representativa (la del primer producto publicado que encuentre),
    para la sección "Explora por categoría" de la landing."""
    cats = get_categories(conn)
    out = []
    for c in cats:
        in_cat = [it for it in items if it["category"] == c["name"]]
        if not in_cat:
            continue
        image = next((it["image"] for it in in_cat if it.get("image")), None)
        out.append({
            "name": c["name"],
            "count": len(in_cat),
            "image": image,
            "url": url_for("public_catalog", cat=c["name"]) + "#pubGrid",
        })
    return out[:limit]


# ---------------------------------------------------------------------------
# Guía de envío (PDF) — media carta, con remitente fijo y destinatario a mano
# ---------------------------------------------------------------------------
SHIP_LABEL_W = 8.5 * 72  # media hoja carta en apaisado: 8.5 x 5.5 in
SHIP_LABEL_H = 5.5 * 72

REMITENTE = {
    "nombre": "SEBASTIAN ARTURO TORRES GOMEZ",
    "cc": "1127053027",
    "correo": "SATG173@GMAIL.COM",
    "celular": "315 248 7601",
    "direccion": "Cra. 7 #6-74, Villa Del Rosario, Cúcuta",
}

_SHIP_INK = HexColor("#06060a")
_SHIP_BLUE = HexColor("#2541ff")
_SHIP_LAV = HexColor("#6c8cff")
_SHIP_MUTED = HexColor("#6e7280")
_SHIP_LINE = HexColor("#dcdde6")


def _draw_bolt(c, x, y, size, color, opacity=1.0):
    """Dibuja un pequeño rayo (lightning bolt) relleno, como el de la marca."""
    pts = [(62, 0), (10, 95), (48, 95), (38, 160), (92, 60), (52, 60)]
    scale = size / 160.0
    c.saveState()
    try:
        c.setFillColor(color, alpha=opacity)
    except TypeError:
        c.setFillColor(color)
    p = c.beginPath()
    p.moveTo(x + pts[0][0] * scale, y + pts[0][1] * scale)
    for px, py in pts[1:]:
        p.lineTo(x + px * scale, y + py * scale)
    p.close()
    c.drawPath(p, fill=1, stroke=0)
    c.restoreState()


def _ship_badge(c, x, y, size):
    """Insignia cuadrada redondeada con el monograma ST, igual al logo nuevo."""
    c.saveState()
    c.setFillColor(_SHIP_BLUE)
    c.roundRect(x, y, size, size, size * 0.22, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", size * 0.52)
    c.drawCentredString(x + size / 2, y + size * 0.32, "ST")
    c.restoreState()


def build_shipping_label_pdf(dest):
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(SHIP_LABEL_W, SHIP_LABEL_H))
    W, H = SHIP_LABEL_W, SHIP_LABEL_H

    # Fondo blanco + banda superior oscura de marca
    c.setFillColor(white)
    c.rect(0, 0, W, H, fill=1, stroke=0)

    band_h = 92
    c.setFillColor(_SHIP_INK)
    c.rect(0, H - band_h, W, band_h, fill=1, stroke=0)
    c.setFillColor(_SHIP_BLUE)
    c.rect(0, H - band_h - 3, W, 3, fill=1, stroke=0)
    _draw_bolt(c, W - 70, H - band_h + 14, 60, _SHIP_LAV, opacity=0.18)

    _ship_badge(c, 22, H - band_h + 18, 56)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(92, H - band_h + 52, "@SPORTSWEAR_ST")
    c.setFillColor(HexColor("#c7cbe8"))
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(92, H - band_h + 32, "GUÍA DE ENVÍO")

    # ---- Remitente (fijo) ----
    top = H - band_h - 24
    c.setFillColor(_SHIP_BLUE)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(28, top, "REMITENTE")
    c.setFillColor(_SHIP_INK)
    c.setFont("Helvetica-Bold", 11.5)
    c.drawString(28, top - 18, f"{REMITENTE['nombre']} — CC: {REMITENTE['cc']}")
    c.setFont("Helvetica", 10)
    c.setFillColor(HexColor("#2a2b35"))
    c.drawString(28, top - 34, f"Correo: {REMITENTE['correo']}  ·  Celular: {REMITENTE['celular']}")
    c.drawString(28, top - 50, REMITENTE["direccion"])

    # ---- Divisor ----
    c.setStrokeColor(_SHIP_LINE)
    c.setLineWidth(1)
    c.line(28, top - 66, W - 28, top - 66)

    # ---- Destinatario (a completar) ----
    c.setFillColor(_SHIP_BLUE)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(28, top - 86, "DESTINATARIO")

    fields = [
        ("NOMBRE", dest.get("nombre", "")),
        ("CC", dest.get("cc", "")),
        ("DIRECCIÓN", dest.get("direccion", "")),
        ("CIUDAD", dest.get("ciudad", "")),
        ("CELULAR", dest.get("celular", "")),
    ]
    y = top - 110
    label_w = 86
    for label, value in fields:
        c.setFillColor(_SHIP_MUTED)
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(28, y, f"{label}:")
        c.setFillColor(_SHIP_INK)
        c.setFont("Helvetica-Bold" if value else "Helvetica", 12.5)
        c.drawString(28 + label_w, y, value if value else "")
        c.setStrokeColor(_SHIP_LINE)
        c.setLineWidth(1)
        c.line(28 + label_w, y - 6, W - 28, y - 6)
        y -= 32

    # ---- Pie ----
    _draw_bolt(c, 24, 14, 20, _SHIP_LAV, opacity=0.6)
    c.setFillColor(_SHIP_MUTED)
    c.setFont("Helvetica", 8)
    c.drawString(52, 20, "sportswear_ST — ropa deportiva para tu estilo")

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()


@app.route("/envios", methods=["GET", "POST"])
def shipping_label():
    if request.method == "POST":
        dest = {
            "nombre": (request.form.get("nombre") or "").strip(),
            "cc": (request.form.get("cc") or "").strip(),
            "direccion": (request.form.get("direccion") or "").strip(),
            "ciudad": (request.form.get("ciudad") or "").strip(),
            "celular": (request.form.get("celular") or "").strip(),
        }
        pdf_bytes = build_shipping_label_pdf(dest)
        safe_name = secure_filename(dest["nombre"]) or "destinatario"
        filename = f"guia_envio_{safe_name}.pdf"
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    return render_template("shipping_label.html", remitente=REMITENTE)


@app.route("/inicio")
def landing_page():
    conn = db()
    try:
        items = public_catalog_items(conn)
        categories = landing_categories(conn, items)
        trending = sorted(items, key=lambda it: it.get("updated") or "", reverse=True)[:8]
        brand = get_settings(conn)
    finally:
        conn.close()
    promo_image_name = brand.get("promo_image") or ""
    if promo_image_name:
        promo_image = image_url(promo_image_name)
    else:
        # Si todavía no han subido una foto propia para la promoción, usamos
        # de una vez la de un producto real del catálogo (se puede cambiar
        # después desde Configuración sin tocar código).
        promo_image = next((it["image"] for it in trending if it.get("image")), None)
    return render_template(
        "landing.html",
        categories=categories,
        trending=trending,
        promo_image=promo_image,
    )


@app.route("/catalogo")
def public_catalog():
    conn = db()
    try:
        items = public_catalog_items(conn)
        categories = get_categories(conn)
    finally:
        conn.close()
    return render_template("catalog_list.html", items=items, categories=categories)


@app.route("/catalogo/producto/<reference>")
def catalog_product(reference):
    conn = db()
    try:
        items = public_catalog_items(conn)
        size_guide = get_size_guide(conn)
    finally:
        conn.close()
    ref_upper = reference.strip().upper()
    item = next((it for it in items if it["ref"].upper() == ref_upper), None)
    related = []
    if item:
        related = [it for it in items if it["category"] == item["category"] and it["ref"].upper() != ref_upper][:4]
    return render_template("catalog_product.html", item=item, items=items, size_guide=size_guide, related=related)


@app.route("/catalogo/pedido")
def catalog_pedido():
    conn = db()
    try:
        items = public_catalog_items(conn)
    finally:
        conn.close()
    return render_template("catalog_pedido.html", items=items)


# ---------------------------------------------------------------------------
# Pedidos web: el carrito del catálogo ("Mi pedido") ya no solo arma un texto
# de WhatsApp — también queda guardado aquí, con un link que el cliente envía
# y que tú puedes abrir para ver el pedido bien organizado. Al confirmarlo,
# se refleja automáticamente en Ventas (sin tocar el inventario).
# ---------------------------------------------------------------------------
@app.post("/api/pedido")
def api_create_order():
    data = request.get_json(silent=True) or {}
    raw_items = data.get("items") or []
    items = []
    total = 0.0
    for it in raw_items:
        try:
            qty = max(1, int(it.get("qty") or 1))
            price = float(it.get("price") or 0)
        except (TypeError, ValueError):
            continue
        ref = (it.get("ref") or "").strip()
        if not ref:
            continue
        line = {
            "ref": ref,
            "type": it.get("type") or "producto",
            "name": (it.get("name") or ref).strip(),
            "color": (it.get("color") or "").strip(),
            "talla": (it.get("talla") or "").strip(),
            "qty": qty,
            "price": price,
            "image": it.get("image") or None,
        }
        items.append(line)
        total += price * qty
    if not items:
        return jsonify({"error": "El pedido está vacío."}), 400

    channel = "mayoristas" if data.get("channel") == "mayoristas" else "catalogo"
    slug = new_order_slug()
    conn = db()
    try:
        # uuid4 hex ya es prácticamente único, pero por si acaso reintentamos
        # con otro slug en el rarísimo caso de choque.
        for _ in range(5):
            try:
                conn.execute(
                    "INSERT INTO orders (slug, channel, customer_name, customer_phone, items_json, total) VALUES (?,?,?,?,?,?)",
                    (slug, channel, (data.get("customer_name") or "").strip()[:120],
                     (data.get("customer_phone") or "").strip()[:40], jsonify_safe(items), total),
                )
                conn.commit()
                break
            except sqlite3.IntegrityError:
                slug = new_order_slug()
        else:
            return jsonify({"error": "No se pudo crear el pedido."}), 500
    finally:
        conn.close()
    return jsonify({"slug": slug, "url": url_for("pedido_detail", slug=slug, _external=True)})


def jsonify_safe(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)


@app.get("/pedido/<slug>")
def pedido_detail(slug):
    conn = db()
    try:
        row = conn.execute("SELECT * FROM orders WHERE slug=?", (slug,)).fetchone()
    finally:
        conn.close()
    if not row:
        return render_template("notfound.html"), 404
    import json
    order = dict(row)
    # Ojo: la clave no puede llamarse "items" — Jinja resuelve order.items como
    # el método dict.items() en vez del valor, y rompe el {% for %} del template.
    order["lines"] = json.loads(order["items_json"] or "[]")
    order["status_label"] = ORDER_STATUS_LABELS.get(order["status"], order["status"])
    return render_template("pedido_detail.html", order=order, is_admin=bool(session.get("admin_logged_in")))


@app.post("/pedidos/<slug>/confirmar")
def pedido_confirm(slug):
    conn = db()
    try:
        row = conn.execute("SELECT * FROM orders WHERE slug=?", (slug,)).fetchone()
        if not row:
            flash("Ese pedido no existe.", "error")
            return redirect(url_for("pedidos_list"))
        order = dict(row)
        if order["status"] == "confirmado":
            flash("Ese pedido ya estaba confirmado.", "ok")
            return redirect(url_for("pedido_detail", slug=slug))
        import json
        items = json.loads(order["items_json"] or "[]")
        costs = product_cost_lookup(conn)
        sale_date = now_str()[:10]
        for it in items:
            qty = it.get("qty") or 1
            unit_cost = costs.get(it.get("ref"), 0)
            name = it["name"]
            variant = " / ".join(filter(None, [it.get("color"), it.get("talla")]))
            if variant:
                name += f" ({variant})"
            conn.execute(
                """INSERT INTO sales (sale_date, product_ref, product_name, quantity, cost, sale_price,
                   shipping_cost, shipping_payer, customer_name, customer_phone, notes)
                   VALUES (?,?,?,?,?,?,0,'ninguno',?,?,?)""",
                (sale_date, it.get("ref", ""), name, qty, unit_cost * qty, it.get("price", 0) * qty,
                 order["customer_name"], order["customer_phone"], f"Pedido web #{slug} confirmado desde el catálogo."),
            )
        conn.execute(
            "UPDATE orders SET status='confirmado', confirmed_at=? WHERE slug=?",
            (now_str(), slug),
        )
        conn.commit()
    finally:
        conn.close()
    flash("Pedido confirmado y registrado en Ventas.", "ok")
    return redirect(url_for("pedido_detail", slug=slug))


@app.post("/pedidos/<slug>/cancelar")
def pedido_cancel(slug):
    conn = db()
    conn.execute("UPDATE orders SET status='cancelado' WHERE slug=?", (slug,))
    conn.commit()
    conn.close()
    flash("Pedido marcado como cancelado.", "ok")
    return redirect(url_for("pedido_detail", slug=slug))


@app.route("/pedidos")
def pedidos_list():
    conn = db()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()]
    finally:
        conn.close()
    import json
    for r in rows:
        r["lines"] = json.loads(r["items_json"] or "[]")
        r["status_label"] = ORDER_STATUS_LABELS.get(r["status"], r["status"])
    return render_template("pedidos_list.html", orders=rows)


@app.get("/api/catalogo/items")
def api_catalog_items():
    conn = db()
    try:
        items = public_catalog_items(conn)
    finally:
        conn.close()
    return jsonify(items)


# ---------------------------------------------------------------------------
# Páginas legales (requisito básico para poder anunciar en Meta/Google y para
# cumplir con la Ley 1581 de 2012 de protección de datos en Colombia).
# ---------------------------------------------------------------------------
@app.route("/politica-de-privacidad")
def privacy_policy():
    return render_template("legal_privacy.html", updated=datetime.now().strftime("%d/%m/%Y"))


@app.route("/terminos-y-condiciones")
def terms_and_conditions():
    return render_template("legal_terms.html", updated=datetime.now().strftime("%d/%m/%Y"))


# ---------------------------------------------------------------------------
# robots.txt y sitemap.xml — ayudan a que Google indexe bien el catálogo y a
# que no intente rastrear el panel de administración.
# ---------------------------------------------------------------------------
@app.get("/robots.txt")
def robots_txt():
    lines = [
        "User-agent: *",
        "Allow: /inicio",
        "Allow: /catalogo",
        "Allow: /mayoristas",
        "Allow: /politica-de-privacidad",
        "Allow: /terminos-y-condiciones",
        "Disallow: /productos",
        "Disallow: /conjuntos",
        "Disallow: /ventas",
        "Disallow: /categorias",
        "Disallow: /tallas",
        "Disallow: /configuracion",
        "Disallow: /envios",
        "Disallow: /pedido/",
        "Disallow: /pedidos",
        "Disallow: /api/",
        f"Sitemap: {request.url_root}sitemap.xml",
    ]
    return Response("\n".join(lines) + "\n", mimetype="text/plain")


@app.get("/sitemap.xml")
def sitemap_xml():
    conn = db()
    try:
        items = public_catalog_items(conn)
    finally:
        conn.close()
    root = request.url_root.rstrip("/")
    static_paths = [
        "/inicio", "/catalogo", "/mayoristas",
        "/politica-de-privacidad", "/terminos-y-condiciones",
    ]
    urls = [f"{root}{p}" for p in static_paths]
    urls += [f"{root}/catalogo/producto/{it['ref']}" for it in items]
    body = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        body.append(f"  <url><loc>{u}</loc></url>")
    body.append("</urlset>")
    return Response("\n".join(body), mimetype="application/xml")


# ---------------------------------------------------------------------------
# Catálogo mayorista (solo lectura, con precio al por mayor en vez de precio
# de venta al público). Un producto o conjunto solo aparece aquí una vez que
# tiene un precio mayorista definido (mayor a 0) — así nunca se muestra un
# $0 por error mientras no lo has configurado.
# ---------------------------------------------------------------------------
def wholesale_catalog_items(conn):
    items = []

    products = conn.execute(
        "SELECT * FROM products WHERE published=1 AND wholesale_price > 0 ORDER BY category, reference"
    ).fetchall()
    for p in products:
        colors = conn.execute("SELECT * FROM colors WHERE product_id=? ORDER BY id", (p["id"],)).fetchall()
        images = []
        if p["image"]:
            images.append(image_url(p["image"]))
        for c in colors:
            if c["image"]:
                u = image_url(c["image"])
                if u not in images:
                    images.append(u)
        items.append({
            "type": "producto",
            "ref": p["reference"],
            "name": p["name"],
            "category": p["category"],
            "price": p["wholesale_price"],
            "image": image_url(p["image"]),
            "images": images,
            "colors": [{"name": c["color"], "image": image_url(c["image"])} for c in colors],
            "sizes": [t.strip() for t in (p["sizes"] or "").split(",") if t.strip()],
            "includes": [],
            "url": url_for("mayorista_product", reference=p["reference"]),
        })

    sets_ = conn.execute("SELECT * FROM sets WHERE published=1 ORDER BY reference").fetchall()
    for s in sets_:
        set_items = _set_items_detail(conn, s["id"])
        if not set_items:
            continue
        wholesale_map = {row["reference"]: row["wholesale_price"] for row in conn.execute("SELECT reference, wholesale_price FROM products")}
        # Precio mayorista del conjunto = suma del precio mayorista de cada
        # prenda que lo compone (por su cantidad). Si a alguna prenda le falta
        # precio mayorista, el conjunto tampoco se muestra todavía.
        missing = any(not wholesale_map.get(it["reference"]) for it in set_items)
        if missing:
            continue
        wholesale_total = sum(wholesale_map.get(it["reference"], 0) * (it["quantity"] or 1) for it in set_items)
        colors = _set_colors(conn, s["id"])
        images = []
        if s["image"]:
            images.append(image_url(s["image"]))
        for c in colors:
            if c["image"]:
                u = image_url(c["image"])
                if u not in images:
                    images.append(u)
        items.append({
            "type": "conjunto",
            "ref": s["reference"],
            "name": s["name"],
            "category": "Conjuntos",
            "price": wholesale_total,
            "image": image_url(s["image"]),
            "images": images,
            "colors": [{"name": c["color"], "image": image_url(c["image"])} for c in colors],
            "sizes": [],
            "includes": [{"ref": it["reference"], "name": it["name"], "quantity": it["quantity"]} for it in set_items],
            "url": url_for("mayorista_product", reference=s["reference"]),
        })
    return items


@app.route("/mayoristas")
def mayoristas_catalog():
    conn = db()
    try:
        items = wholesale_catalog_items(conn)
        categories = get_categories(conn)
        settings_ = get_settings(conn)
    finally:
        conn.close()
    return render_template(
        "mayoristas_list.html", items=items, categories=categories,
        min_qty=settings_.get("wholesale_min_qty") or "6",
        wholesale_note=settings_.get("wholesale_note") or "",
    )


@app.route("/mayoristas/producto/<reference>")
def mayorista_product(reference):
    conn = db()
    try:
        items = wholesale_catalog_items(conn)
        settings_ = get_settings(conn)
        size_guide = get_size_guide(conn)
    finally:
        conn.close()
    ref_upper = reference.strip().upper()
    item = next((it for it in items if it["ref"].upper() == ref_upper), None)
    related = []
    if item:
        related = [it for it in items if it["category"] == item["category"] and it["ref"].upper() != ref_upper][:4]
    return render_template(
        "mayoristas_product.html", item=item, items=items,
        min_qty=settings_.get("wholesale_min_qty") or "6",
        size_guide=size_guide, related=related,
    )


# ---------------------------------------------------------------------------
# Importar precios al por mayor desde un CSV (columnas: reference,wholesale_price)
# Útil para cargar de una vez los precios de muchas referencias sin escribirlos
# uno por uno en el formulario de cada producto.
# ---------------------------------------------------------------------------
@app.post("/productos/importar-mayorista")
def import_wholesale_prices():
    file = request.files.get("wholesale_csv")
    if not file or not file.filename:
        flash("Selecciona un archivo CSV para importar.", "error")
        return redirect(request.referrer or url_for("products_list"))

    try:
        text = file.stream.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        flash("No se pudo leer el archivo. Debe ser un CSV de texto (UTF-8).", "error")
        return redirect(request.referrer or url_for("products_list"))

    reader = csv.DictReader(io.StringIO(text))
    fieldmap = {(name or "").strip().lower(): name for name in (reader.fieldnames or [])}
    ref_col = fieldmap.get("reference") or fieldmap.get("referencia") or fieldmap.get("ref")
    price_col = fieldmap.get("wholesale_price") or fieldmap.get("precio_mayorista") or fieldmap.get("precio mayorista")
    if not ref_col or not price_col:
        flash("El CSV debe tener las columnas 'reference' y 'wholesale_price' (o 'referencia' y 'precio_mayorista').", "error")
        return redirect(request.referrer or url_for("products_list"))

    conn = db()
    updated, not_found = 0, []
    try:
        for row in reader:
            ref = (row.get(ref_col) or "").strip().upper()
            price_raw = (row.get(price_col) or "").strip().replace("$", "").replace(".", "").replace(",", "")
            if not ref:
                continue
            try:
                price = float(price_raw or 0)
            except ValueError:
                continue
            cur = conn.execute("UPDATE products SET wholesale_price=? WHERE UPPER(reference)=?", (price, ref))
            if cur.rowcount:
                updated += 1
            else:
                not_found.append(ref)
        conn.commit()
    finally:
        conn.close()

    msg = f"Precios mayoristas actualizados en {updated} producto(s)."
    if not_found:
        msg += f" No se encontraron estas referencias: {', '.join(not_found[:15])}" + (" ..." if len(not_found) > 15 else "")
    flash(msg, "ok" if updated else "error")
    return redirect(request.referrer or url_for("products_list"))


# ---------------------------------------------------------------------------
# API auxiliar (usada por los formularios de conjuntos)
# ---------------------------------------------------------------------------
@app.get("/api/products")
def api_products():
    conn = db()
    rows = conn.execute("SELECT id,reference,category,name,cost,sale_price,sizes FROM products ORDER BY reference").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.get("/api/product/<int:pid>/colors")
def api_colors(pid):
    conn = db()
    rows = conn.execute("SELECT * FROM colors WHERE product_id=? ORDER BY id", (pid,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)

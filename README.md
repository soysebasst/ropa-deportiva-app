# sportswear_ST — Administrador + Catálogo público

## Qué incluye

### Administrador (privado, para ti)
- Categorías dinámicas: Tops, Shorts, Camisas, Conjuntos y las que tú agregues.
- Referencia única para filtrar y buscar productos.
- Imagen principal de cada prenda + imagen por color.
- Precio de costo y precio de venta (el costo NUNCA se muestra al público).
- Tallas y colores.
- Creación de conjuntos usando referencias existentes, con cálculo automático del costo y precio de venta independiente.
- Control de Publicado / Oculto por producto y por conjunto.
- Configuración de tienda: logo, nombre, descripción corta, tagline del catálogo, Instagram y número de WhatsApp.
- Base de datos SQLite local (`inventario.db`). Imágenes guardadas en la carpeta `uploads`.
- NO maneja stock ni existencias — nunca lo hará por diseño.

### Catálogo público (para tus clientes)
- Vitrina de productos y conjuntos PUBLICADOS, en `/catalogo`.
- Filtro solo por categoría (sin buscador de texto) + ordenamiento (recientes, precio, nombre).
- Ficha de producto con galería, selección de color (cambia la foto) y talla, en `/catalogo/producto/<referencia>`.
- "Mi pedido": selección persistente en el navegador (localStorage), NO es un carrito de compra ni hay pago en línea.
- Envío del pedido por WhatsApp con mensaje pre-armado; consulta individual por producto también por WhatsApp.
- Compartir/copiar enlace de un producto específico.
- Diseño 100% distinto al administrador (mobile-first, colores e identidad de marca).

## Instalación en Windows (uso local)
1. Instala Python 3.11 o superior.
2. Abre esta carpeta.
3. Ejecuta `run_windows.bat`.
4. Se abrirá el servidor local en `http://127.0.0.1:5000` — pedirá iniciar sesión (ver "Inicio de sesión" abajo).
5. El catálogo público queda disponible en `http://127.0.0.1:5000/catalogo`, sin pedir sesión.
6. La información queda guardada en `inventario.db`.

## Inicio de sesión del administrador
El administrador está protegido con usuario y contraseña. El catálogo público (`/catalogo` y todo lo que cuelga de ahí) sigue siendo visible sin iniciar sesión.

- En tu computador, si no configuras nada, las credenciales de prueba son `admin` / `admin`.
- En producción se definen con las variables de entorno `ADMIN_USERNAME` y `ADMIN_PASSWORD` — nunca quedan escritas en el código. **No publiques la app en internet sin cambiarlas.**

## Configurar el catálogo
Ve a **Configuración** en el administrador y completa: logo, nombre de tienda, descripción corta, texto del catálogo (tagline), Instagram y WhatsApp. El catálogo público toma estos datos automáticamente — no hay que tocar código.

## Publicar / ocultar productos
En la lista de productos y de conjuntos, y también dentro de la ficha de cada uno, hay un botón para alternar entre **Publicado** y **Oculto**. Solo lo Publicado aparece en `/catalogo`.

## Publicar en internet (Render)
El proyecto ya está preparado para desplegarse en Render (servidor `gunicorn`, disco persistente para los datos, login del administrador). Variables de entorno a configurar en el servicio:

| Variable | Para qué sirve |
|---|---|
| `ADMIN_USERNAME` | Tu usuario de administrador |
| `ADMIN_PASSWORD` | Tu contraseña de administrador |
| `SECRET_KEY` | Una clave propia y secreta para las sesiones |
| `DATA_DIR` | Ruta del disco persistente, ej. `/data` |

La guía completa, paso a paso (GitHub, Render, dominio propio, compartir el catálogo), quedó entregada como un informe aparte.

## Importante
- Nunca definas `ADMIN_PASSWORD` ni `SECRET_KEY` directamente en `app.py` — siempre por variable de entorno.
- Haz copias de seguridad de `inventario.db` y de la carpeta `uploads` de vez en cuando, además de los respaldos automáticos que ofrezca tu plataforma de despliegue.
- Mientras el servidor corra solo en `http://127.0.0.1:5000`, el catálogo es visible únicamente en el computador donde se ejecuta. Para que tus clientes lo vean desde internet, sigue la guía de despliegue.

/* sportswear_ST — catálogo público: carrito "Mi pedido" persistente en localStorage.
   No es un carrito de compra: no hay pago ni checkout. Solo organiza una selección
   que el cliente envía por WhatsApp para que la tienda la confirme manualmente. */

(function (global) {
  "use strict";

  var STORAGE_KEY = "sportswearst_pedido_v1";

  // Carga los productos/conjuntos publicados que el servidor incrustó en la página
  // (script#catalog-data). Siempre viene fresco desde la base de datos en cada carga,
  // así el carrito nunca muestra precios/nombres/imágenes desactualizados.
  var dataEl = document.getElementById("catalog-data");
  var CATALOG_ITEMS = [];
  if (dataEl) {
    try { CATALOG_ITEMS = JSON.parse(dataEl.textContent) || []; } catch (e) { CATALOG_ITEMS = []; }
  }
  global.CATALOG_ITEMS = CATALOG_ITEMS;

  var COLOR_HEX = {
    "negro": "#111111", "black": "#111111",
    "blanco": "#ffffff", "white": "#ffffff",
    "rosado": "#f472b6", "rosa": "#f472b6", "pink": "#f472b6",
    "azul": "#2d42ff", "blue": "#2d42ff", "celeste": "#7dd3fc", "navy": "#1e3a8a",
    "rojo": "#ef4444", "red": "#ef4444",
    "verde": "#22c55e", "green": "#22c55e",
    "gris": "#9ca3af", "grey": "#9ca3af", "gray": "#9ca3af",
    "amarillo": "#eab308", "yellow": "#eab308",
    "naranja": "#f97316", "orange": "#f97316",
    "morado": "#a855f7", "purple": "#a855f7", "violeta": "#a855f7", "lila": "#c4b5fd",
    "beige": "#e7d9c4", "crema": "#f5ecd9",
    "café": "#7c4a2d", "cafe": "#7c4a2d", "marron": "#7c4a2d", "marrón": "#7c4a2d", "vinotinto": "#7f1d3a",
    "fucsia": "#e0249a", "turquesa": "#14b8a6", "dorado": "#d4af37", "plateado": "#c0c0c0",
  };
  function colorHex(name) {
    return COLOR_HEX[(name || "").trim().toLowerCase()] || "#97a2ff";
  }

  function money(v) {
    var n = Number(v) || 0;
    return "$" + Math.round(n).toLocaleString("es-CO");
  }

  function findItem(type, ref) {
    return CATALOG_ITEMS.find(function (i) { return i.type === type && i.ref === ref; });
  }

  function load() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      var parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
      return [];
    }
  }

  function save(lines) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(lines)); } catch (e) { /* almacenamiento no disponible */ }
    updateBadge();
  }

  function sameLine(a, b) {
    return a.type === b.type && a.ref === b.ref && a.color === b.color && a.talla === b.talla;
  }

  function add(entry) {
    var lines = load();
    var existing = lines.find(function (l) { return sameLine(l, entry); });
    if (existing) {
      existing.qty = (existing.qty || 1) + (entry.qty || 1);
    } else {
      lines.push({ type: entry.type, ref: entry.ref, color: entry.color || "", talla: entry.talla || "", qty: entry.qty || 1 });
    }
    save(lines);
    return lines;
  }

  function setQty(index, qty) {
    var lines = load();
    if (!lines[index]) return lines;
    if (qty <= 0) {
      lines.splice(index, 1);
    } else {
      lines[index].qty = qty;
    }
    save(lines);
    return lines;
  }

  function removeAt(index) {
    return setQty(index, 0);
  }

  function clear() {
    save([]);
  }

  function resolved() {
    return load().map(function (line) {
      var item = findItem(line.type, line.ref);
      return {
        line: line,
        found: !!item,
        name: item ? item.name : line.ref,
        category: item ? item.category : "",
        price: item ? item.price : 0,
        image: item ? (item.image || (item.images && item.images[0])) : null,
        url: item ? item.url : null,
      };
    });
  }

  function totalCount() {
    return load().reduce(function (sum, l) { return sum + (l.qty || 0); }, 0);
  }

  function totalPrice() {
    return resolved().reduce(function (sum, r) { return sum + (r.price || 0) * (r.line.qty || 0); }, 0);
  }

  function updateBadge() {
    var count = totalCount();
    document.querySelectorAll("#cartFabCount, .cart-fab-count").forEach(function (el) {
      el.textContent = count;
    });
    document.querySelectorAll("#cartFab, .cart-fab").forEach(function (el) {
      el.classList.toggle("has-items", count > 0);
    });
  }

  function toast(msg) {
    var el = document.createElement("div");
    el.className = "sw-toast";
    el.textContent = msg;
    document.body.appendChild(el);
    requestAnimationFrame(function () { el.classList.add("show"); });
    setTimeout(function () {
      el.classList.remove("show");
      setTimeout(function () { el.remove(); }, 300);
    }, 2200);
  }

  // ---- Mensajes de WhatsApp -------------------------------------------------
  function waLink(text) {
    var number = (global.BRAND && global.BRAND.whatsapp) || "";
    var base = number ? ("https://wa.me/" + number) : "https://wa.me/";
    return base + "?text=" + encodeURIComponent(text);
  }

  function buildOrderMessage() {
    var brandName = (global.BRAND && global.BRAND.name) || "la tienda";
    var lines = resolved();
    var text = "Hola " + brandName + " 👋\n";
    text += "Quiero consultar por el siguiente pedido:\n\n";
    lines.forEach(function (r, i) {
      text += (i + 1) + ". " + r.line.ref + " - " + r.name + "\n";
      text += "Color: " + (r.line.color || "Por seleccionar") + "\n";
      text += "Talla: " + (r.line.talla || "Por seleccionar") + "\n";
      text += "Cantidad: " + r.line.qty + "\n";
      if (r.line.qty > 1) {
        text += "Precio unitario: " + money(r.price) + "\n\n";
      } else {
        text += "Precio: " + money(r.price) + "\n\n";
      }
    });
    text += "Total referencial: " + money(totalPrice()) + "\n\n";
    text += "Quedo atento/a para confirmar mi pedido.\n";
    text += "(Esta selección no representa una compra confirmada.)";
    return text;
  }

  function buildProductMessage(item, color, talla) {
    var brandName = (global.BRAND && global.BRAND.name) || "la tienda";
    var text = "Hola " + brandName + " 👋\n";
    text += "Estoy interesado/a en:\n";
    text += item.ref + "\n";
    text += item.name + "\n";
    text += "Color: " + (color || "Por seleccionar") + "\n";
    text += "Talla: " + (talla || "Por seleccionar") + "\n";
    text += "Precio: " + money(item.price) + "\n\n";
    text += "Quisiera más información.";
    return text;
  }

  function sendOrder() {
    if (totalCount() === 0) {
      toast("Tu pedido está vacío.");
      return;
    }
    global.open(waLink(buildOrderMessage()), "_blank");
  }

  function sendProductInquiry(item, color, talla) {
    global.open(waLink(buildProductMessage(item, color, talla)), "_blank");
  }

  // ---- Compartir --------------------------------------------------------
  function shareOrCopy(url, title, text) {
    if (navigator.share) {
      navigator.share({ title: title, text: text || title, url: url }).catch(function () {});
      return;
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(function () {
        toast("Enlace copiado. Puedes pegarlo en WhatsApp o Instagram.");
      }).catch(function () {
        toast("Copia el enlace desde la barra del navegador para compartirlo.");
      });
      return;
    }
    toast("Puedes tomar una captura de pantalla y enviarla por WhatsApp.");
  }

  global.SWCart = {
    STORAGE_KEY: STORAGE_KEY,
    load: load, save: save, add: add, setQty: setQty, removeAt: removeAt, clear: clear,
    resolved: resolved, totalCount: totalCount, totalPrice: totalPrice,
    updateBadge: updateBadge, toast: toast, money: money, colorHex: colorHex,
    waLink: waLink, buildOrderMessage: buildOrderMessage, buildProductMessage: buildProductMessage,
    sendOrder: sendOrder, sendProductInquiry: sendProductInquiry, shareOrCopy: shareOrCopy,
    findItem: findItem,
  };

  document.addEventListener("DOMContentLoaded", updateBadge);
})(window);

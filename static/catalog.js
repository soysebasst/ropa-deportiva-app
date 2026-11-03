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
    // Aviso para la capa de animación (botón "✓ Agregado", rebote del contador).
    try { document.dispatchEvent(new CustomEvent("swcart:add", { detail: entry })); } catch (e) { /* navegador antiguo */ }
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
        hasColors: !!(item && item.colors && item.colors.length),
        hasSizes: !!(item && item.type !== "conjunto" && item.sizes && item.sizes.length),
      };
    });
  }

  function totalCount() {
    return load().reduce(function (sum, l) { return sum + (l.qty || 0); }, 0);
  }

  function totalPrice() {
    return resolved().reduce(function (sum, r) { return sum + (r.price || 0) * (r.line.qty || 0); }, 0);
  }

  var lastBadgeCount = null;
  function updateBadge() {
    var count = totalCount();
    var grew = lastBadgeCount !== null && count > lastBadgeCount;
    lastBadgeCount = count;
    document.querySelectorAll("#cartFabCount, .cart-fab-count").forEach(function (el) {
      el.textContent = count;
      if (grew) {
        // Rebote corto del contador cuando se agrega algo al pedido.
        el.classList.remove("bump");
        void el.offsetWidth;
        el.classList.add("bump");
      }
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
      if (r.hasColors) text += "Color: " + (r.line.color || "Por seleccionar") + "\n";
      if (r.hasSizes) text += "Talla: " + (r.line.talla || "Por seleccionar") + "\n";
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
    if (item.colors && item.colors.length) text += "Color: " + (color || "Por seleccionar") + "\n";
    if (item.type !== "conjunto" && item.sizes && item.sizes.length) text += "Talla: " + (talla || "Por seleccionar") + "\n";
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

/* =========================================================
   CAPA DE MOVIMIENTO — aditiva, no cambia la lógica del pedido.
   Principios: animar solo transform/opacity, curvas fuertes de salida,
   menos de 300 ms en interacciones y respeto por "reducir movimiento".
   ========================================================= */
(function () {
  "use strict";
  window.__swMotionReady = true;

  var EASE_OUT = "cubic-bezier(.23,1,.32,1)";
  var reduceMotion = false;
  try { reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) { reduceMotion = false; }
  var canAnimate = !!(Element.prototype.animate) && !reduceMotion;

  function ready(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  // ---- 1. Revelado escalonado de tarjetas al hacer scroll -----------------
  function setupReveal() {
    var cards = Array.prototype.slice.call(document.querySelectorAll(".pub-grid .pub-card"));
    if (!cards.length) return;
    var root = document.documentElement;
    if (!canAnimate || !("IntersectionObserver" in window)) {
      root.classList.remove("js-motion");
      return;
    }
    root.classList.add("js-motion");

    function show(card, delay) {
      card.style.setProperty("--reveal-delay", delay + "ms");
      card.classList.add("reveal-in");
      // Al terminar se quita el retraso para que el hover responda al instante.
      setTimeout(function () { card.style.removeProperty("--reveal-delay"); }, 560 + delay);
    }

    var batch = 0, batchTimer = null;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        io.unobserve(entry.target);
        show(entry.target, Math.min(batch, 8) * 55);
        batch++;
      });
      clearTimeout(batchTimer);
      batchTimer = setTimeout(function () { batch = 0; }, 120);
    }, { rootMargin: "0px 0px -6% 0px", threshold: 0.08 });

    cards.forEach(function (c) { io.observe(c); });
  }

  // ---- 1b. Revelado genérico (tarjeta flotante, encabezados de sección) ---
  function setupGenericReveal() {
    var els = Array.prototype.slice.call(document.querySelectorAll(".reveal-up"));
    if (!els.length) return;
    var root = document.documentElement;
    if (!canAnimate || !("IntersectionObserver" in window)) {
      root.classList.remove("js-motion");
      return;
    }
    root.classList.add("js-motion");
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        io.unobserve(entry.target);
        entry.target.classList.add("reveal-in");
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    els.forEach(function (el) { io.observe(el); });
  }

  // ---- 2. Filtro de categorías: las tarjetas visibles entran suave --------
  function setupFilterMotion() {
    var nav = document.getElementById("catNav");
    var grid = document.getElementById("pubGrid");
    if (!nav || !grid) return;
    nav.addEventListener("click", function (e) {
      var chip = e.target.closest(".cat-chip");
      if (!chip) return;
      // Lleva el chip activo a la vista en la barra horizontal (celular).
      try { chip.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", inline: "center", block: "nearest" }); } catch (err) { /* sin soporte */ }
      if (!canAnimate) return;
      // El filtro original ya se aplicó en este mismo clic; animamos lo que quedó visible.
      requestAnimationFrame(function () {
        var visible = Array.prototype.filter.call(grid.querySelectorAll(".pub-card"), function (c) {
          return c.style.display !== "none";
        });
        visible.forEach(function (card, i) {
          card.classList.add("reveal-in");
          card.animate(
            [{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "translateY(0)" }],
            { duration: 260, delay: Math.min(i, 10) * 25, easing: EASE_OUT, fill: "backwards" }
          );
        });
      });
    });
  }

  // ---- 3. Foto principal: crossfade con blur al cambiar color/miniatura ----
  function setupImageCrossfade() {
    var img = document.getElementById("pdpMainImage");
    if (!img || !canAnimate || !("MutationObserver" in window)) return;
    var mo = new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        if (muts[i].attributeName !== "src" || muts[i].oldValue === img.getAttribute("src")) continue;
        var run = function () {
          img.animate(
            [{ opacity: 0.35, filter: "blur(6px)", transform: "scale(1.015)" },
             { opacity: 1, filter: "blur(0px)", transform: "scale(1)" }],
            { duration: 240, easing: EASE_OUT }
          );
        };
        if (img.complete) run(); else img.addEventListener("load", run, { once: true });
        break;
      }
    });
    mo.observe(img, { attributes: true, attributeFilter: ["src"], attributeOldValue: true });
  }

  // ---- 4. "Agregar al pedido": el botón confirma con "✓ Agregado" ---------
  function setupAddedState() {
    var btn = document.getElementById("addToOrderBtn");
    if (!btn) return;
    var original = btn.textContent.trim();
    btn.textContent = "";
    var label = document.createElement("span");
    label.className = "btn-label";
    label.textContent = original;
    btn.appendChild(label);
    var timer = null;

    function swapTo(text, added) {
      if (!canAnimate) {
        label.textContent = text;
        btn.classList.toggle("is-added", added);
        return;
      }
      label.classList.add("swapping");
      setTimeout(function () {
        label.textContent = text;
        btn.classList.toggle("is-added", added);
        label.classList.remove("swapping");
      }, 140);
    }

    document.addEventListener("swcart:add", function () {
      clearTimeout(timer);
      swapTo("✓ Agregado", true);
      timer = setTimeout(function () { swapTo(original, false); }, 1600);
    });
  }

  ready(function () {
    setupReveal();
    setupGenericReveal();
    setupFilterMotion();
    setupImageCrossfade();
    setupAddedState();
  });
})();

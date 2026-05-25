document.addEventListener("DOMContentLoaded", () => {
  const singlePanel = document.getElementById("single-panel");
  const mvPanel = document.getElementById("mv-panel");
  const singleGallery = document.getElementById("gallery");
  const mvGallery = document.getElementById("gallery-mv");
  const singleHeading = singlePanel.querySelector(".panel-heading");
  const mvHeading = mvPanel.querySelector(".panel-heading");
  const searchInput = document.getElementById("search");
  const fileInput = document.getElementById("file-input");
  const modeRadios = document.querySelectorAll('input[name="mode"]');

  const currentMode = () => document.querySelector('input[name="mode"]:checked').value;

  const fetchJson = async (url, init) => {
    const res = await fetch(url, init);
    if (!res.ok) {
      let detail = "";
      try { detail = (await res.text()).slice(0, 200); } catch (_) {}
      throw new Error(`${url} ${res.status}${detail ? `: ${detail}` : ""}`);
    }
    return res.json();
  };

  const renderError = (gallery, message) => {
    gallery.innerHTML = "";
    const p = document.createElement("p");
    p.className = "error";
    p.textContent = message;
    gallery.appendChild(p);
  };

  const closeLightbox = () => {
    const overlay = document.getElementById("lightbox-overlay");
    if (overlay) overlay.classList.remove("open");
  };

  const openLightbox = (url, alt) => {
    let overlay = document.getElementById("lightbox-overlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.id = "lightbox-overlay";
      overlay.className = "lightbox-overlay";
      overlay.addEventListener("click", closeLightbox);
      document.body.appendChild(overlay);
    }
    overlay.innerHTML = "";
    const full = document.createElement("img");
    full.src = url;
    full.alt = alt || "";
    full.className = "lightbox-image";
    overlay.appendChild(full);
    overlay.classList.add("open");
  };

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeLightbox();
  });

  const renderResults = (gallery, items) => {
    gallery.innerHTML = "";
    if (!items || items.length === 0) {
      gallery.innerHTML = "<p>No results.</p>";
      return;
    }
    items.forEach(item => {
      const card = document.createElement("div");
      card.className = "result-card";

      const img = document.createElement("img");
      img.src = item.url;
      img.alt = item.filename;
      img.title = item.score != null ? `score: ${item.score.toFixed(4)}` : item.filename;
      img.className = "thumbnail";
      img.addEventListener("click", () => openLightbox(item.url, item.description || item.filename));
      card.appendChild(img);

      if (item.description) {
        const caption = document.createElement("p");
        caption.className = "result-caption";
        caption.textContent = item.description;
        caption.title = item.description;
        card.appendChild(caption);
      }

      gallery.appendChild(card);
    });
  };

  const applyModeVisibility = () => {
    const mode = currentMode();
    const showSingle = mode === "single" || mode === "both";
    const showMv = mode === "mv" || mode === "both";
    singlePanel.hidden = !showSingle;
    mvPanel.hidden = !showMv;
    singleHeading.hidden = mode !== "both";
    mvHeading.hidden = mode !== "both";
  };

  const loadLatest = () => {
    fetchJson("/latest")
      .then(data => renderResults(singleGallery, data.results))
      .catch(err => {
        console.error("Failed to load images:", err);
        renderError(singleGallery, `Error loading images: ${err.message}`);
      });
  };

  const runSearch = (query) => {
    if (!query) return;
    const mode = currentMode();
    const targets = [];
    if (mode === "single" || mode === "both") {
      targets.push({ url: `/search?text=${encodeURIComponent(query)}`, gallery: singleGallery });
    }
    if (mode === "mv" || mode === "both") {
      targets.push({ url: `/search-mv?text=${encodeURIComponent(query)}`, gallery: mvGallery });
    }
    targets.forEach(t => {
      fetchJson(t.url)
        .then(data => renderResults(t.gallery, data.results))
        .catch(err => {
          console.error(`Search failed (${t.url}):`, err);
          renderError(t.gallery, `Search failed: ${err.message}`);
        });
    });
  };

  applyModeVisibility();
  if (currentMode() === "single") loadLatest();

  modeRadios.forEach(r => r.addEventListener("change", () => {
    applyModeVisibility();
    const q = searchInput.value.trim();
    if (q) {
      runSearch(q);
    } else if (currentMode() === "single") {
      loadLatest();
    } else {
      singleGallery.innerHTML = "";
      mvGallery.innerHTML = "";
    }
  }));

  let searchTimeout;
  searchInput.addEventListener("input", () => {
    clearTimeout(searchTimeout);
    const query = searchInput.value.trim();
    if (!query) return;
    searchTimeout = setTimeout(() => runSearch(query), 300);
  });

  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    if (!file) return;

    const mode = currentMode();
    const endpoints = [];
    if (mode === "single" || mode === "both") endpoints.push("/upload");
    if (mode === "mv" || mode === "both") endpoints.push("/upload-mv");

    Promise.all(endpoints.map(ep => {
      const fd = new FormData();
      fd.append("file", file);
      return fetchJson(ep, { method: "POST", body: fd });
    }))
      .then(() => {
        alert(`Upload complete (${endpoints.join(", ")}).`);
        window.location.reload();
      })
      .catch(err => {
        console.error("Upload failed:", err);
        alert(`Upload failed: ${err.message}`);
      });
  });
});

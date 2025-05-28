document.addEventListener("DOMContentLoaded", () => {
  const gallery = document.getElementById("gallery");
  const searchInput = document.getElementById("search");
  const fileInput = document.getElementById("file-input");

  // Fetch and display the latest 9 images
  fetch("https://search.metabare.com/latest")
    .then(res => res.json())
    .then(data => {
      gallery.innerHTML = "";
      data.results.forEach(item => {
        const img = document.createElement("img");
        img.src = item.url;
        img.alt = item.filename;
        img.className = "thumbnail";
        gallery.appendChild(img);
      });
    })
    .catch(err => {
      console.error("Failed to load images:", err);
      gallery.innerHTML = "<p>Error loading images.</p>";
    });
    let searchTimeout;
    searchInput.addEventListener("input", () => {
      clearTimeout(searchTimeout);
      const query = searchInput.value.trim();
      if (!query) return;

      searchTimeout = setTimeout(() => {
        fetch(`https://search.metabare.com/search?text=${encodeURIComponent(query)}`)
          .then(res => res.json())
          .then(data => {
            gallery.innerHTML = "";
            data.results.forEach(item => {
              const img = document.createElement("img");
              img.src = item.url;
              img.alt = item.filename;
              img.className = "thumbnail";
              gallery.appendChild(img);
            });
          })
          .catch(err => {
            console.error("Search failed:", err);
            gallery.innerHTML = "<p>Error loading search results.</p>";
          });
      }, 300); // debounce time in ms
    });


  // Optional: placeholder upload handler
  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append("file", file);

    fetch("https://upload.metabare.com/upload", {
      method: "POST",
      body: formData,
    })
      .then(res => res.json())
      .then(data => {
        alert("Upload complete.");
        window.location.reload(); // Reload to see new image
      })
      .catch(err => {
        console.error("Upload failed:", err);
        alert("Upload failed.");
      });
  });
});

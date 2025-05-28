fetch("https://upload.metabare.com/storage/files")
  .then(res => res.json())
  .then(data => {
    const container = document.getElementById("lance-container");

    const grouped = {};

    // Group files by top-level folder name
    data.lance.files.forEach(file => {
      const parts = file.name.split("/");
      const group = parts.length > 1 ? parts[0] : "(root)";
      if (!grouped[group]) grouped[group] = [];
      grouped[group].push(file);
    });

    Object.entries(grouped).forEach(([folder, files]) => {
      const section = document.createElement("div");
      section.style.marginBottom = "2em";

      const heading = document.createElement("h2");
      heading.textContent = folder;
      section.appendChild(heading);

      const table = document.createElement("table");

      const thead = document.createElement("thead");
      thead.innerHTML = `<tr><th>File</th><th>Size</th><th>Modified</th></tr>`;
      table.appendChild(thead);

      const tbody = document.createElement("tbody");
      table.appendChild(tbody);

      files.forEach(file => {
        const row = document.createElement("tr");
        const filename = file.name.split("/").slice(1).join("/") || file.name;
        row.innerHTML = `
          <td>${filename}</td>
          <td>${(file.size / 1024).toFixed(1)} KB</td>
          <td>${new Date(file.modified).toLocaleString()}</td>
        `;
        tbody.appendChild(row);
      });


      section.appendChild(table);
      container.appendChild(section);
    });

    // Summary at bottom
    const summary = document.createElement("p");
    summary.style.marginTop = "2em";
    summary.style.fontStyle = "italic";
    summary.textContent = `${data.lance.count} files — ${(data.lance.total_size / 1024).toFixed(1)} KB total`;
    container.appendChild(summary);
  });

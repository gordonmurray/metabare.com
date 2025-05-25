const files = [
  { filename: "building.jpg", url: "https://picsum.photos/seed/forest/600/400" },
  { filename: "bench.jpg", url: "https://picsum.photos/seed/sunset/600/400" },
  { filename: "snow_path.jpg", url: "https://picsum.photos/seed/coffee/600/400" },
];

let filtered = [...files];

const gallery = document.getElementById("gallery");
const search = document.getElementById("search");

function trackClick(filename) {
  console.log('Tracking click for:', filename);
  // POST tracking data to /api/track (for future backend)
  fetch('/api/track', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      action: 'image_click',
      filename: filename,
      timestamp: new Date().toISOString()
    })
  }).catch(error => {
    console.log('Tracking request failed (expected if no backend):', error);
  });
}

function render() {
  gallery.innerHTML = "";
  filtered.forEach(file => {
    const div = document.createElement("div");
    div.className = "card";
    div.innerHTML = `
      <img src="${file.url}" alt="${file.filename}" />
      <div class="filename">${file.filename}</div>
    `;

    // Add click event listener to the image
    const img = div.querySelector('img');
    img.addEventListener('click', () => trackClick(file.filename));

    gallery.appendChild(div);
  });
}

search.addEventListener("input", async () => {
  const q = search.value.toLowerCase();

  if (q.trim() === '') {
    filtered = [...files];
    render();
    return;
  }

  try {
    // Search via API
    const response = await fetch(`https://search.metabare.com/search?text=${encodeURIComponent(q)}`);
    if (response.ok) {
      const searchResults = await response.json();
      // Expecting { results: [...] }
      if (searchResults && Array.isArray(searchResults.results)) {
        filtered = searchResults.results.map(r => ({
          filename: r.id || r.filename || 'unknown',
          url: r.url || '',
          ...r
        }));
      } else {
        filtered = [];
      }
    } else {
      // Fallback to local search
      filtered = files.filter(f => f.filename.toLowerCase().includes(q));
    }
  } catch (error) {
    console.log('Search API failed, using local search:', error);
    // Fallback to local search
    filtered = files.filter(f => f.filename.toLowerCase().includes(q));
  }

  render();
});

document.getElementById("file-input").addEventListener("change", async e => {
  const file = e.target.files[0];
  if (file) {
    try {
      // Show upload progress
      const uploadBtn = document.querySelector('.upload-btn');
      const fileInput = document.getElementById("file-input");
      uploadBtn.textContent = 'Uploading...';
      uploadBtn.style.opacity = '0.6';

      // Create FormData and append the file
      const formData = new FormData();
      formData.append('file', file);

      // POST to your API endpoint
      const response = await fetch('https://upload.metabare.com/upload', {
        method: 'POST',
        body: formData
      });

      if (response.ok) {
        const result = await response.json();
        console.log('Upload successful:', result);

        // Add the uploaded file to the gallery
        const newFile = {
          filename: file.name,
          url: URL.createObjectURL(file)
        };
        files.push(newFile);
        filtered = [...files];
        render();

        // No alert for success - just console log
      } else {
        throw new Error(`Upload failed: ${response.status} ${response.statusText}`);
      }
    } catch (error) {
      console.error('Upload error:', error);
      alert(`Upload failed: ${error.message}`);
    } finally {
      // Reset upload button and file input
      const uploadBtn = document.querySelector('.upload-btn');
      const fileInput = document.getElementById("file-input");
      uploadBtn.textContent = 'Upload';
      uploadBtn.style.opacity = '1';

      // Clear the file input to allow same file upload again
      fileInput.value = '';
    }
  }
});

render();
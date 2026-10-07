// CloudFront Function, viewer request: S3 has no directory index, so a request
// for /spike or /spike/ is rewritten to /spike/index.html. /spike redirects to
// /spike/ first, keeping its query string, so relative URLs in the page
// resolve under the directory.
function query(qs) {
  var parts = [];
  for (var key in qs) {
    var entry = qs[key];
    var values = entry.multiValue ? entry.multiValue : [entry];
    for (var i = 0; i < values.length; i++) {
      var v = values[i].value;
      parts.push(v === "" ? key : key + "=" + v);
    }
  }
  return parts.length ? "?" + parts.join("&") : "";
}

function handler(event) {
  var request = event.request;
  var uri = request.uri;
  if (uri.endsWith("/")) {
    request.uri = uri + "index.html";
  } else if (uri.lastIndexOf(".") <= uri.lastIndexOf("/")) {
    return {
      statusCode: 301,
      statusDescription: "Moved Permanently",
      headers: { location: { value: uri + "/" + query(request.querystring) } },
    };
  }
  return request;
}

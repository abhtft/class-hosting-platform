// Used by the multi-tenant distribution (custom domains). The tenant's origin path
// (/sites/<student>) already selects the student, so this only does the SPA fallback:
// a last path segment without a file extension is a client-side route -> /index.html
function handler(event) {
  var request = event.request;
  var last = request.uri.slice(request.uri.lastIndexOf('/') + 1);
  if (last.indexOf('.') === -1) request.uri = '/index.html';
  return request;
}

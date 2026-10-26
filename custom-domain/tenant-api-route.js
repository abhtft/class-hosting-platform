// CloudFront function `student-hosting-tenant-api`, attached (viewer-request) to the /api/* behavior of the multi-tenant
// distribution that serves student CUSTOM DOMAINS (see custom-domain/HANDOFF.md and backend_wire.md).
//
// The API origin's path is /b/{{student}}, which already selects the student's backend, so all that is left to do is:
//   * remove the "/api" prefix:   /api/analyse  ->  /analyse    (the origin path then makes it /b/<student>/analyse)
//   * pass the real visitor IP in x-viewer-ip, overwriting anything the client sent. The gateway trusts it only together
//     with the secret x-origin-secret header that the distribution adds to this origin.
function handler(event) {
  var request = event.request;
  var rest = request.uri.slice('/api'.length);
  request.uri = rest.length ? rest : '/';
  request.headers['x-viewer-ip'] = { value: event.viewer.ip };
  return request;
}

const headers = {'Content-Type': 'application/json'};

export function createApi(fetcher = fetch) {
  async function call(path, options = {}) {
    const response = await fetcher(path, {headers, ...options});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || `Request failed (${response.status})`);
    return value;
  }

  return {
    dashboard: () => call('/api/dashboard'),
    requests: () => call('/api/requests'),
    request: id => call(`/api/requests/${id}`),
    work: () => call('/api/work'),
    createRequest: values => call('/api/requests', {
      method: 'POST', body: JSON.stringify({command_id: crypto.randomUUID(), ...values}),
    }),
    submitRequest: request => call(`/api/requests/${request.id}/submit`, {
      method: 'POST', body: JSON.stringify({
        command_id: crypto.randomUUID(), expected_revision: request.revision,
      }),
    }),
    createAssessment: (request, values) => call(`/api/requests/${request.id}/assessments`, {
      method: 'POST', body: JSON.stringify({
        command_id: crypto.randomUUID(), expected_revision: request.revision, ...values,
      }),
    }),
    act: (work, action) => call(`/api/work/${work.step_instance_id}/actions`, {
      method: 'POST', body: JSON.stringify({
        command_id: crypto.randomUUID(), action, expected_revision: work.owner_revision,
        payload: {},
      }),
    }),
  };
}

export const api = createApi();

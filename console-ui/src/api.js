async function req(method, url, body) {
  const opt = { method, headers: {} }
  if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json'
    opt.body = JSON.stringify(body)
  }
  const resp = await fetch(url, opt)
  let data = null
  try { data = await resp.json() } catch {}
  if (resp.status === 409) {
    const err = new Error(data?.detail || 'conflict')
    err.conflict = true
    throw err
  }
  if (!resp.ok) throw new Error(data?.detail || `HTTP ${resp.status}`)
  return data
}

export default {
  tree: () => req('GET', '/api/tree'),
  scenario: (p) => req('GET', `/api/scenarios/${p}`),
  save: (p, body) => req('PUT', `/api/scenarios/${p}`, body),
  validate: (data) => req('POST', '/api/validate', { data }),
  parse: (raw) => req('POST', '/api/parse', { raw }),
  run: (env, module) => req('POST', '/api/run', { env, module }),
  runs: () => req('GET', '/api/runs'),
  environments: () => req('GET', '/api/environments'),
  saveEnvironments: (raw) => req('PUT', '/api/environments', { raw }),
  modules: () => req('GET', '/api/modules'),
  saveModules: (raw) => req('PUT', '/api/modules', { raw }),
}

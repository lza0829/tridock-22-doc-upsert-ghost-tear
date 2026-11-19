export async function api(path, opts = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  })
  const text = await res.text()
  let data
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!res.ok) {
    const detail = data && data.detail !== undefined ? data.detail : text
    const err = new Error(
      typeof detail === 'string'
        ? detail
        : (detail && detail.message) || JSON.stringify(detail),
    )
    err.status = res.status
    if (detail && typeof detail === 'object') {
      err.code = detail.code
      err.existing = detail.existing
    }
    throw err
  }
  return data
}

const API = 'http://localhost:8000'

export async function autosave(filename: string, content: string, folder: string = 'workspace') {
  if (!content.trim()) return
  try {
    await fetch(`${API}/api/files/autosave`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename, content, folder })
    })
  } catch {}
}

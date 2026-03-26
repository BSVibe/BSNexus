/**
 * Parse an SSE stream from a fetch Response, dispatching each event to a callback.
 *
 * Handles buffering across chunks, multi-line data fields, and the
 * `event:` / `data:` SSE protocol.
 */
export async function parseSSEStream(
  response: Response,
  onEvent: (event: string, data: string) => void,
): Promise<void> {
  const reader = response.body?.getReader()
  if (!reader) throw new Error('No response body')

  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    let currentEvent = ''
    let dataLines: string[] = []
    for (const line of lines) {
      if (line.startsWith('event:')) {
        currentEvent = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trim())
      } else if (line === '' || line === '\r') {
        if (currentEvent && dataLines.length > 0) {
          onEvent(currentEvent, dataLines.join('\n'))
        }
        currentEvent = ''
        dataLines = []
      }
    }
  }
}

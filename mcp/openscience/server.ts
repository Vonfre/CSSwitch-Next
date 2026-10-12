// CSSwitch MCP wrapper around the Apache-2.0 OpenScience database adapters.
import { createInterface } from 'node:readline'
import { registry } from './connectors'
import { HttpStatusError } from './connectors/http'
import z from 'zod'

if (Number(process.versions.node.split('.')[0]) < 22) throw new Error('OpenScience MCP requires Node.js 22 or later')
const schemas = {
  science_list_dbs: z.object({ domain: z.string().optional() }).strict(),
  science_search: z.object({ db: z.string().min(1), query: z.string().min(1).max(4000),
    limit: z.number().int().min(1).max(50).default(10), organism: z.string().max(200).optional() }).strict(),
  science_fetch: z.object({ db: z.string().min(1), id: z.string().min(1).max(2000), format: z.string().optional() }).strict(),
}
const tools = Object.entries(schemas).map(([name, schema]) => ({ name,
  description: name === 'science_list_dbs' ? 'List scientific databases, domains and supported file formats. Some sources require their own API key in the server environment.' :
    name === 'science_search' ? 'Search a scientific database by db ID from science_list_dbs. Returns IDs, titles, summaries and URLs. Source failures are errors, not empty results.' :
    'Fetch a database record by db and id. Optional format returns a text file (for example fasta/cif/sdf). Large results require a narrower query; no files are written.',
  inputSchema: z.toJSONSchema(schema), annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true },
}))

function result(value: unknown, isError = false) {
  const text = JSON.stringify(value)
  if (Buffer.byteLength(text) > 2 * 1024 * 1024) return { isError: true, content: [{ type: 'text', text: '{"error":"result_too_large","message":"Result exceeds 2 MiB; narrow the query or choose another format."}' }] }
  return { content: [{ type: 'text', text }], isError }
}
function cleanError(error: unknown) {
  let message = error instanceof Error ? error.message : 'Database request failed'
  for (const [k, value] of Object.entries(process.env)) {
    if (/key|token|secret|password/i.test(k) && value) {
      message = message.replaceAll(value, '[REDACTED]').replaceAll(encodeURIComponent(value), '[REDACTED]')
    }
  }
  return message.slice(0, 1500)
}
async function invoke(name: string, input: unknown, signal: AbortSignal) {
  const schema = schemas[name as keyof typeof schemas]
  if (!schema) throw new Error('Unknown tool')
  const params: any = schema.parse(input ?? {})
  if (name === 'science_list_dbs') return result({ databases: registry.catalog().filter(c => !params.domain || c.domain === params.domain) })
  const c = registry.get(params.db)
  if (!c) return result({ error: 'unknown_db', message: 'Use science_list_dbs for supported database IDs' }, true)
  if (name === 'science_search') return result({ db: c.id, results: await c.search(params.query, { ...params, signal }) })
  const format = params.format?.trim().toLowerCase()
  if (format && !c.formats?.includes(format)) return result({ error: 'unsupported_format', formats: c.formats || [] }, true)
  if (format && c.fetchFile) {
    const file = await c.fetchFile(params.id, format, { signal })
    return result({ db: c.id, id: params.id, format, file })
  }
  const record: any = await c.fetch(params.id, { signal })
  if (record?.error) return result({ db: c.id, error: 'source_error', message: cleanError(new Error(record.error)) }, true)
  const found = record != null && !(typeof record === 'object' && (record.found === false || Object.keys(record).length === 0))
  return result({ db: c.id, id: params.id, found, record })
}

const active = new Map<string | number, AbortController>()
async function handle(req: any) {
  if (!req || req.jsonrpc !== '2.0' || typeof req.method !== 'string') return
  if (req.method === 'notifications/cancelled') { active.get(req.params?.requestId)?.abort(); return }
  if (req.id === undefined) return
  const send = (value: any) => process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: req.id, ...value }) + '\n')
  if (req.method === 'initialize') return send({ result: { protocolVersion: ['2024-11-05', '2025-03-26', '2025-06-18'].includes(req.params?.protocolVersion) ? req.params.protocolVersion : '2024-11-05', capabilities: { tools: {} }, serverInfo: { name: 'csswitch-openscience', version: '1.0.0' } } })
  if (req.method === 'ping') return send({ result: {} })
  if (req.method === 'tools/list') return send({ result: { tools } })
  if (req.method !== 'tools/call') return send({ error: { code: -32601, message: 'Method not found' } })
  if (active.size >= 4) return send({ result: result({ error: 'busy', message: 'Four database requests already running; retry later.' }, true) })
  const controller = new AbortController()
  active.set(req.id, controller)
  const timer = setTimeout(() => controller.abort(), 90_000)
  try { send({ result: await invoke(req.params?.name, req.params?.arguments, controller.signal) }) }
  catch (e) { send({ result: result({ error: 'source_error', message: e instanceof z.ZodError ? 'Invalid tool arguments' : cleanError(e),
    ...(e instanceof HttpStatusError ? { status: e.status, retryable: e.status === 429 || e.status >= 500 } : {}) }, true) }) }
  finally { clearTimeout(timer); active.delete(req.id) }
}
const lines = createInterface({ input: process.stdin, crlfDelay: Infinity })
lines.on('line', line => {
  if (Buffer.byteLength(line) > 1024 * 1024) return
  try { void handle(JSON.parse(line)) } catch { process.stdout.write('{"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"Parse error"}}\n') }
})
lines.on('close', () => { for (const c of active.values()) c.abort(); process.exit(0) })

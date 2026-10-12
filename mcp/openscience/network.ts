// CSSwitch standalone replacement for OpenScience's application Network module.
// Node 22+, including HTTPS_PROXY support for Science's local gateway.
import { lookup } from 'node:dns/promises'
import { isIP } from 'node:net'
import { Agent, EnvHttpProxyAgent, fetch as undiciFetch } from 'undici'

// Science sandboxes stdio children to its HTTPS proxy. DNS then belongs to the
// proxy, so avoid direct DNS there and restrict destinations to adapter hosts.
const databaseHosts = new Set([
  'alphafold.ebi.ac.uk', 'api.biorxiv.org', 'api.biosimulators.org', 'api.crossref.org',
  'api.genome.ucsc.edu', 'api.openalex.org', 'api.platform.opentargets.org', 'api.semanticscholar.org',
  'arxiv.org', 'bindingdb.org', 'data.rcsb.org', 'depmap.org', 'doi.org', 'europepmc.org',
  'eutils.ncbi.nlm.nih.gov', 'export.arxiv.org', 'files.rcsb.org', 'gnomad.broadinstitute.org',
  'gtexportal.org', 'mygene.info', 'myvariant.info', 'reactome.org', 'rest.ensembl.org',
  'rest.kegg.jp', 'rest.uniprot.org', 'string-db.org', 'webservice.thebiogrid.org',
  'pubchem.ncbi.nlm.nih.gov', 'www.bindingdb.org', 'www.biorxiv.org', 'www.ebi.ac.uk',
  'www.guidetopharmacology.org', 'www.ncbi.nlm.nih.gov', 'www.proteinatlas.org',
  'www.rcsb.org', 'www.surechembl.org', 'www.wikipathways.org',
])
const proxied = (url: URL) => Boolean(process.env.https_proxy || process.env.HTTPS_PROXY || process.env.http_proxy || process.env.HTTP_PROXY)
  && !(process.env.no_proxy || process.env.NO_PROXY || '').split(',').some(entry => {
    const host = entry.trim().toLowerCase().replace(/:\d+$/, '')
    return host === '*' || url.hostname === host || (host.startsWith('.') && url.hostname.endsWith(host))
  })

function publicAddress(address: string): boolean {
  if (isIP(address) === 4) {
    const [a, b] = address.split('.').map(Number)
    return !(a === 0 || a === 10 || a === 127 || a >= 224 ||
      (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) ||
      (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127))
  }
  // Reject mapped/local IPv6; public global unicast currently uses 2000::/3.
  return isIP(address) === 6 && /^[23]/.test(address)
}

async function addresses(url: string) {
  const u = new URL(url)
  if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password ||
      (u.port && !['80', '443'].includes(u.port))) throw new Error('Unsupported database endpoint')
  const host = u.hostname.replace(/^\[|\]$/g, '')
  if (!databaseHosts.has(host)) throw new Error('Endpoint is outside the scientific database host list')
  if (proxied(u)) return [] // Trusted configured proxy resolves approved hosts.
  const results = isIP(host) ? [{ address: host, family: isIP(host) }] : await lookup(host, { all: true })
  if (!results.length || results.some(r => !publicAddress(r.address))) throw new Error('Database endpoint must use a public address')
  return results
}

// Check DNS again at connection time, preventing a public-to-local DNS switch.
const connect = { lookup(host: string, opts: any, done: any) {
  lookup(host, { all: true }).then(results => {
    if (results.some(r => !publicAddress(r.address))) return done(new Error('Non-public database address'))
    const eligible = results.filter(r => !opts.family || r.family === opts.family)
    if (!eligible.length) return done(new Error('No database address'))
    if (opts.all) done(null, eligible)
    else done(null, eligible[0].address, eligible[0].family)
  }, done)
}}
// The proxy itself may be loopback (CSSwitch); only destination addresses are checked.
const dispatcher = new EnvHttpProxyAgent({ connect, proxyTls: {}, requestTls: {} })
const direct = new Agent({ connect })

export namespace Network {
  export interface FetchPolicy { resolveAddresses?: unknown; transport?: unknown }
  export async function assertAllowed(url: string) { await addresses(url) }
  export async function fetch(url: string, options: any, _policy?: FetchPolicy): Promise<Response> {
    let target = url
    let headers = options.headers
    let method = options.method
    let body = options.body
    for (let n = 0; n <= 5; n++) {
      await assertAllowed(target)
      const response = await undiciFetch(target, { ...options, headers, method, body, redirect: 'manual',
        dispatcher: process.env.HTTPS_PROXY || process.env.https_proxy || process.env.HTTP_PROXY || process.env.http_proxy ? dispatcher : direct })
      if ([301, 302, 303, 307, 308].includes(response.status) && response.headers.has('location')) {
        await response.body?.cancel()
        const next = new URL(response.headers.get('location')!, target).href
        if (new URL(next).origin !== new URL(target).origin) {
          headers = Object.fromEntries(Object.entries(headers || {}).filter(([k]) => !/authorization|key|token|cookie/i.test(k)))
        }
        if (response.status === 303 || ([301, 302].includes(response.status) && method === 'POST')) { method = 'GET'; body = undefined }
        target = next
        continue
      }
      const reader = response.body?.getReader()
      const chunks: Uint8Array[] = []
      let bytes = 0
      if (reader) try {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          bytes += value.byteLength
          if (bytes > 16 * 1024 * 1024) throw new Error('Database response exceeds 16 MiB')
          chunks.push(value)
        }
      } finally { await reader.cancel() }
      return new Response([204, 205, 304].includes(response.status) ? null : Buffer.concat(chunks), {
        status: response.status, statusText: response.statusText, headers: response.headers as any,
      })
    }
    throw new Error('Too many database redirects')
  }
}

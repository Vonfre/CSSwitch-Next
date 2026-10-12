#!/usr/bin/env python3
"""Read-only stdio MCP for public scientific APIs; no Claude login required.

Add as a local connector with command=/absolute/path/to/python3 and
args=[/absolute/path/to/science-public-mcp.py]. Uses Python's standard library
and the normal HTTPS_PROXY/NO_PROXY environment when present.
"""
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

VERSION = "0.1.0"
NCBI = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
ANNOTATIONS = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True}
TOOLS = [
    {
        "name": "connector_probe",
        "description": "Check that this local scientific connector is reachable.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {**ANNOTATIONS, "openWorldHint": False},
    },
    {
        "name": "pubmed_search",
        "description": "Search NCBI PubMed and return real PMID identifiers, titles and source links.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 512},
                "limit": {"type": "integer", "minimum": 1, "maximum": 5, "default": 2},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "annotations": ANNOTATIONS,
    },
    {
        "name": "uniprot_fetch",
        "description": "Fetch a UniProtKB protein by accession, including gene, organism and function.",
        "inputSchema": {
            "type": "object",
            "properties": {"accession": {"type": "string", "pattern": "^[A-Z0-9]{6,10}$"}},
            "required": ["accession"],
            "additionalProperties": False,
        },
        "annotations": ANNOTATIONS,
    },
]


def get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": f"CSSwitch-Public-MCP/{VERSION}", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = response.read(4 * 1024 * 1024 + 1)
    if len(payload) > 4 * 1024 * 1024:
        raise ValueError("Public API response exceeds 4 MiB")
    return json.loads(payload)


def pubmed_search(arguments):
    query = arguments.get("query")
    limit = arguments.get("limit", 2)
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 512:
        raise ValueError("query must contain 1–512 characters")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5:
        raise ValueError("limit must be an integer from 1 to 5")
    search = get_json(NCBI + "esearch.fcgi?" + urllib.parse.urlencode({
        "db": "pubmed", "term": query.strip(), "retmode": "json", "retmax": limit, "sort": "relevance",
    }))["esearchresult"]
    identifiers = search["idlist"]
    result = {"source": "NCBI PubMed", "query": query.strip(), "count": int(search["count"]), "results": []}
    if not identifiers:
        return result
    records = get_json(NCBI + "esummary.fcgi?" + urllib.parse.urlencode({
        "db": "pubmed", "id": ",".join(identifiers), "retmode": "json",
    }))["result"]
    for identifier in identifiers:
        record = records[identifier]
        result["results"].append({
            "pmid": identifier, "title": record["title"], "published": record.get("pubdate"),
            "journal": record.get("fulljournalname"), "url": f"https://pubmed.ncbi.nlm.nih.gov/{identifier}/",
        })
    return result


def uniprot_fetch(arguments):
    accession = arguments.get("accession")
    if not isinstance(accession, str) or not re.fullmatch(r"[A-Z0-9]{6,10}", accession):
        raise ValueError("accession must be a UniProtKB accession such as P04637")
    record = get_json(f"https://rest.uniprot.org/uniprotkb/{accession}.json")
    recommended = record.get("proteinDescription", {}).get("recommendedName", {})
    return {
        "source": "UniProtKB", "accession": record["primaryAccession"], "entry": record.get("uniProtkbId"),
        "protein_name": recommended.get("fullName", {}).get("value"),
        "genes": [gene.get("geneName", {}).get("value") for gene in record.get("genes", []) if gene.get("geneName")],
        "organism": record.get("organism", {}).get("scientificName"),
        "sequence_length": record.get("sequence", {}).get("length"),
        "function": [text["value"] for comment in record.get("comments", []) if comment.get("commentType") == "FUNCTION" for text in comment.get("texts", [])],
        "url": f"https://www.uniprot.org/uniprotkb/{record['primaryAccession']}/entry",
    }


def call_tool(name, arguments):
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    handlers = {"pubmed_search": pubmed_search, "uniprot_fetch": uniprot_fetch}
    if name == "connector_probe":
        result = {"status": "CSSWITCH_MCP_PROBE_OK", "version": VERSION, "services": ["PubMed", "UniProt"]}
    elif name in handlers:
        result = handlers[name](arguments)
    else:
        raise ValueError(f"Unknown tool: {name}")
    return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False}


def handle(request):
    method = request.get("method")
    if method == "initialize":
        protocol = request.get("params", {}).get("protocolVersion")
        supported = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
        return {"protocolVersion": protocol if protocol in supported else supported[0], "capabilities": {"tools": {}}, "serverInfo": {"name": "csswitch-public-science", "version": VERSION}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        params = request.get("params", {})
        try:
            return call_tool(params.get("name"), params.get("arguments", {}))
        except (ValueError, KeyError, urllib.error.URLError, TimeoutError, OSError) as error:
            return {"content": [{"type": "text", "text": f"Scientific API request failed: {error}"}], "isError": True}
    raise ValueError(f"Unsupported method: {method}")


def main():
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Request must be an object")
            if "id" not in request:
                continue
            response = {"jsonrpc": "2.0", "id": request["id"], "result": handle(request)}
        except (ValueError, KeyError, TypeError) as error:
            response = {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None, "error": {"code": -32600, "message": str(error)}}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

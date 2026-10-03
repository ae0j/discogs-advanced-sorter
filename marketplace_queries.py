"""Read approved GraphQL query definitions from the public Marketplace client.

Discogs allowlists its frontend's query hashes, so an arbitrary replacement
query is not accepted. Parse the published documents as data (never execute
JavaScript), apply Apollo's __typename transform, and require the resulting
hash to appear in the same bundle's registry before using it.
"""

import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from graphql import GraphQLError, Visitor, parse, print_ast, visit
from graphql.language.ast import (
    FieldNode,
    NameNode,
    OperationDefinitionNode,
    OperationType,
    SelectionSetNode,
)


SUPPORTED_OPERATIONS = frozenset({"MarketplaceSearch", "Styles"})
MAX_CLIENT_ASSETS = 8
_TEMPLATE = re.compile(
    r"[\w$]+`(\s*query\s+(?:MarketplaceSearch|Styles)\b(?:\\.|[^`\\])*)`"
)
_HASH_LITERAL = re.compile(r"[\"'`]([a-f0-9]{64})[\"'`]")


class QueryDiscoveryError(ValueError):
    pass


@dataclass(frozen=True)
class QueryDefinition:
    document: str
    sha256_hash: str


class _ClientScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.sources.append(attrs["src"])
        elif tag == "link" and attrs.get("rel") == "modulepreload":
            self.sources.append(attrs.get("href", ""))


def client_asset_urls(page_html, page_url):
    parser = _ClientScripts()
    parser.feed(page_html)
    origin = urlparse(page_url)
    assets = []
    for source in parser.sources:
        url = urljoin(page_url, source)
        parsed = urlparse(url)
        # Never follow third-party scripts or paths supplied by a pasted URL.
        if (
            parsed.scheme != "https"
            or parsed.netloc != origin.netloc
            or not re.fullmatch(r"/shop/list/assets/[\w.-]+\.js", parsed.path)
            or parsed.query
            or parsed.fragment
        ):
            continue
        if url not in assets:
            assets.append(url)
    if not assets or len(assets) > MAX_CLIENT_ASSETS:
        raise QueryDiscoveryError("Cannot identify the current Marketplace client.")
    return assets


class _BrowserTypenames(Visitor):
    def enter_selection_set(self, node, key, parent, path, ancestors):
        # Match Apollo's addTypenameToDocument, including inline fragments.
        if isinstance(parent, OperationDefinitionNode):
            return None
        if any(
            isinstance(selection, FieldNode)
            and selection.name.value.startswith("__")
            for selection in node.selections
        ):
            return None
        if isinstance(parent, FieldNode) and any(
            directive.name.value == "export" for directive in (parent.directives or ())
        ):
            return None
        return SelectionSetNode(
            selections=(
                *node.selections,
                FieldNode(name=NameNode(value="__typename")),
            )
        )


def query_definitions_from_bundle(bundle):
    registered = set(_HASH_LITERAL.findall(bundle))
    definitions = {}
    for match in _TEMPLATE.finditer(bundle):
        source = match.group(1)
        # Templates requiring JavaScript interpolation/escape evaluation are
        # deliberately unsupported, rather than evaluated or guessed at.
        if "${" in source or "\\" in source:
            continue
        try:
            document = parse(source, no_location=True, max_tokens=20000)
        except GraphQLError:
            continue
        if len(document.definitions) != 1:
            continue
        operation = document.definitions[0]
        if (
            not isinstance(operation, OperationDefinitionNode)
            or operation.operation != OperationType.QUERY
            or not operation.name
            or operation.name.value not in SUPPORTED_OPERATIONS
        ):
            continue
        canonical = print_ast(visit(document, _BrowserTypenames()))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if digest not in registered:
            continue
        name = operation.name.value
        definition = QueryDefinition(canonical, digest)
        if name in definitions and definitions[name] != definition:
            raise QueryDiscoveryError(f"Ambiguous {name} query definitions.")
        definitions[name] = definition
    return definitions

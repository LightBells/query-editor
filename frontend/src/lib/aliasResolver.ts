// Resolve DSL aliases → table names by scanning the editor text.
// Function style, incl. qualified refs:
//   `u = from(users)`              → { u: "users" }
//   `u = from(analysis_test.users)` → { u: "users" }   (last segment, for column lookup)
//   `u = from('proj.ds.users')`    → { u: "users" }

const SOURCE_RE =
  /([A-Za-z_]\w*)\s*=\s*(?:from|join|agg_lateral|agg_lateral_grouped|apply|lateral)_?\s*\(\s*['"`]?([A-Za-z_][\w.-]*)/g;

export function buildAliasMap(text: string): Record<string, string> {
  const map: Record<string, string> = {};
  let m: RegExpExecArray | null;
  SOURCE_RE.lastIndex = 0;
  while ((m = SOURCE_RE.exec(text)) !== null) {
    const [, alias, path] = m;
    map[alias] = path.split(".").pop() as string; // last segment = table name
  }
  return map;
}

export function resolveAlias(alias: string, text: string): string | undefined {
  return buildAliasMap(text)[alias];
}

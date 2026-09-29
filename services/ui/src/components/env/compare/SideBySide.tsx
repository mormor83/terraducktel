// Monaco side-by-side diff — imported lazily by RawDiff so the Compare page
// doesn't pull Monaco until someone asks for it.
import { RegoDiff } from "../../RegoEditor";

export default function SideBySide({ original, modified }: { original: string; modified: string }) {
  return <RegoDiff original={original} modified={modified} height={420} />;
}

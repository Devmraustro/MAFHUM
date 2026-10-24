import type { DocumentStatus } from "@/lib/api";
import { useLocale } from "@/lib/locale-context";

/**
 * Processing-status pill.
 *
 * The label is text, not colour alone, so the state stays legible to a screen
 * reader and to a colour-blind reader.
 */
export function StatusBadge({ status }: { status: DocumentStatus }) {
  const { t } = useLocale();
  return (
    <span className={`badge badge-${status}`}>
      {t(`status.${status}` as const)}
    </span>
  );
}

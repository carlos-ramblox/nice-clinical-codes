"use client";

import { useState } from "react";
import { rejectCodelistV2 } from "@/lib/api";
import { ConfirmModal } from "../../ConfirmModal";

interface RejectModalProps {
  codelistId: string;
  open: boolean;
  onClose: () => void;
  onRejected: () => void;
}

/**
 * Reject confirmation. v2-only — single-reviewer rejection is a
 * unilateral veto by design (Watson 2017: any reviewer can withdraw
 * consensus). Reason required server-side; the textarea enforces a
 * non-whitespace string client-side too.
 *
 * Built on top of the existing ConfirmModal so focus-trap, escape,
 * backdrop-click and accessibility behaviours come for free.
 */
export function RejectModal({
  codelistId,
  open,
  onClose,
  onRejected,
}: RejectModalProps) {
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmed = reason.trim();
  const canSubmit = trimmed.length > 0 && !submitting;

  const handleConfirm = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      await rejectCodelistV2(codelistId, trimmed);
      // Reset for next open and tell parent to refetch.
      setReason("");
      onRejected();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSubmitting(false);
    }
  };

  const handleCancel = () => {
    if (submitting) return;
    setReason("");
    setError(null);
    onClose();
  };

  return (
    <ConfirmModal
      open={open}
      title="Reject this codelist?"
      confirmLabel="Reject"
      loadingLabel="Rejecting…"
      cancelLabel="Cancel"
      variant="danger"
      loading={submitting}
      confirmDisabled={!canSubmit}
      onConfirm={handleConfirm}
      onCancel={handleCancel}
    >
      <p className="mb-3">
        Rejection is a unilateral veto and terminates the review. The
        codelist will not be approved and the rejection reason is
        recorded in the audit log.
      </p>
      <label className="block">
        <span className="text-xs text-gray-700 mb-1 block">
          Reason (required)
        </span>
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={3}
          placeholder="Why is this codelist being rejected?"
          className="w-full px-2 py-1 border border-gray-300 rounded text-sm"
        />
      </label>
      {error && (
        <p className="mt-2 text-xs text-red-700">{error}</p>
      )}
    </ConfirmModal>
  );
}

import { ImageResponse } from "next/og";

export const alt =
  "Clinical Code Discovery: multi-source pipeline with LLM-assisted scoring and human review";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: "72px 80px",
          background: "linear-gradient(135deg, #00436C 0%, #005EA5 100%)",
          color: "#FBFAF8",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div
            style={{
              fontSize: 22,
              letterSpacing: 4,
              textTransform: "uppercase",
              color: "#A8D5F2",
              fontWeight: 600,
            }}
          >
            clinicalcodes.uk
          </div>
          <div
            style={{
              fontSize: 14,
              fontWeight: 700,
              padding: "4px 10px",
              background: "#FBFAF8",
              color: "#00436C",
              borderRadius: 4,
              letterSpacing: 1,
            }}
          >
            BETA
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
          <div
            style={{
              fontSize: 80,
              fontWeight: 600,
              lineHeight: 1.05,
              letterSpacing: -1.5,
            }}
          >
            Clinical Code Discovery
          </div>
          <div
            style={{
              fontSize: 32,
              lineHeight: 1.3,
              color: "#E5EEF5",
              maxWidth: 980,
              fontWeight: 400,
            }}
          >
            Multi-source codelist discovery with LLM-assisted scoring and
            clinician review. SNOMED CT · ICD-10 · OPCS-4
          </div>
        </div>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            fontSize: 20,
            color: "#A8D5F2",
            fontWeight: 500,
            borderTop: "1px solid rgba(168, 213, 242, 0.3)",
            paddingTop: 24,
          }}
        >
          <div>University of Cambridge · PACE Data Science capstone</div>
          <div>Apache-2.0</div>
        </div>
      </div>
    ),
    { ...size },
  );
}

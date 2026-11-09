import { useEffect, useState } from "react";
import { openExternal } from "./links.js";

// Direct video files play here; facebook.com video pages only play inside Meta.
export const playable = url => !!url && !/facebook\.com|fb\.watch/.test(url);

// In-dashboard ad preview: plays the ad's video (Triple Whale video_url), or shows
// the image for static ads, with a button to the ad in Ads Manager.
export default function AdPreview({ ad, onClose }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setFailed(false);
    const onKey = e => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ad, onClose]);
  if (!ad) return null;
  const video = playable(ad.video) && !failed;
  return (
    <div onClick={e => { e.stopPropagation(); onClose(); }} style={{ position: "fixed", inset: 0, zIndex: 1100, background: "rgba(17,24,39,0.72)", display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>
      <div onClick={e => e.stopPropagation()} style={{ background: "#fff", borderRadius: 12, width: "100%", maxWidth: 440, maxHeight: "94vh", display: "flex", flexDirection: "column", overflow: "hidden", boxShadow: "0 20px 50px rgba(0,0,0,0.35)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 12px", borderBottom: "1px solid #E5E7EB" }}>
          <div title={ad.name} style={{ flex: 1, minWidth: 0, fontSize: 12, fontWeight: 600, color: "#1F2937", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{ad.name}</div>
          <button onClick={onClose} aria-label="Close" style={{ border: "none", background: "none", fontSize: 20, lineHeight: 1, cursor: "pointer", color: "#6B7280" }}>×</button>
        </div>
        <div style={{ background: "#111827", display: "flex", alignItems: "center", justifyContent: "center", minHeight: 240, flex: 1, overflow: "hidden" }}>
          {video ? (
            <video key={ad.video} src={ad.video} poster={ad.image || undefined} controls autoPlay muted playsInline
              onError={() => setFailed(true)} style={{ display: "block", maxWidth: "100%", maxHeight: "74vh" }} />
          ) : ad.image ? (
            <img src={ad.image} alt="" referrerPolicy="no-referrer" style={{ display: "block", maxWidth: "100%", maxHeight: "74vh", objectFit: "contain" }} />
          ) : <div style={{ color: "#9CA3AF", fontSize: 12, padding: 24 }}>No preview available.</div>}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 12px", borderTop: "1px solid #E5E7EB" }}>
          <span style={{ flex: 1, fontSize: 11, color: "#6B7280" }}>
            {video ? "Video starts muted; use the speaker icon for sound." : ad.video ? "This video only plays inside Meta. Open it in Ads Manager." : "Static ad (no video)."}
          </span>
          {ad.managerUrl && (
            <a href={ad.managerUrl} target="_blank" rel="noopener noreferrer" onClick={e => openExternal(e, ad.managerUrl)}
              style={{ fontSize: 12, fontWeight: 600, color: "#fff", background: "#244134", borderRadius: 6, padding: "6px 10px", textDecoration: "none", whiteSpace: "nowrap" }}>Open in Ads Manager ↗</a>
          )}
        </div>
      </div>
    </div>
  );
}

// Small ▶ badge over a thumbnail that has a video.
export const PlayBadge = () => (
  <span style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", pointerEvents: "none" }}>
    <span style={{ width: 20, height: 20, borderRadius: "50%", background: "rgba(0,0,0,0.6)", color: "#fff", fontSize: 9, display: "flex", alignItems: "center", justifyContent: "center", paddingLeft: 2 }}>▶</span>
  </span>
);

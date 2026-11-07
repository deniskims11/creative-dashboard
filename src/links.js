// Shared Meta link helpers (ad list modal + Attribution tab).

// Opens Meta links in a new tab ourselves: a plain target="_blank" click was being
// swallowed in some browsers (nothing happened). If the new tab is blocked, open here.
export function openExternal(e, url) {
  if (!url || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
  e.preventDefault();
  const w = window.open(url, "_blank");
  if (w) { try { w.opener = null; } catch {} } else window.location.assign(url);
}

// Ads Manager link that opens the ad inside its own ad account and the Natural Rems
// business, so viewers with several businesses don't land in their personal one.
export const NR_BUSINESS = "1824899207956323";
export function adsManagerUrl(account, adIds) {
  const ids = (adIds || []).filter(Boolean);
  if (!ids.length) return null;
  const act = String(account || "act_944402037249956").replace(/^act_/, "");
  // The ad's edit view shows its preview and also opens archived/deleted ads.
  return `https://adsmanager.facebook.com/adsmanager/manage/ads/edit/standalone?act=${act}&business_id=${NR_BUSINESS}&global_scope_id=${NR_BUSINESS}&selected_ad_ids=${ids[0]}`;
}

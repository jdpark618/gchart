/* 마케팅 추적: Google 광고주 찾기 (11/13 고도화). 광고 투명성 센터 페이지 안에서 실행 (ad_match.py 가 읽어 evaluate).
   입력 [names, hints, domains, ids, since]
     names   이름 검색어 (법인 표기를 뗀 알맹이 + 원래 이름 · 한글/영문 모두) — ad_terms.name_terms
     hints   패키지명의 회사 부분 — 이 이름으로 추천되는 '도메인'만 받음
     domains 웹사이트 · 판매자 주소 · 문의 메일의 도메인
     ids     소재 미리보기에서 찾을 값 (Play 패키지 · 'id' + iOS 앱 ID)
     since   이 시각 이후에 노출된 소재만 미리보기
   순서: ① 이름 추천 → 광고주 후보 + (이름과 같은) 도메인  ② 도메인으로 광고하는 소재 → 광고주 후보  ③ 그 광고주 이름의 알맹이로 다시 이름 추천
        ④ 후보 줄 세우기: 이름이 같은 계정 → 회사 도메인으로 광고 중인 계정 → 이름이 비슷한 계정 → 도메인에서 나온 이름으로 찾은 계정 (같으면 한국 계정 먼저) · 소재가 있는 계정 16개까지  ⑤ 계정마다 소재 미리보기에 패키지/iOS ID 가 있는지 확인 (있는 계정은 모두 확정) */
async ([names, hints, domains, ids, since]) => {
  const rpc = async (name, req) => (await fetch(`/anji/_/rpc/${name}?authuser=`, { method: 'POST', headers: { 'content-type': 'application/x-www-form-urlencoded' }, body: 'f.req=' + encodeURIComponent(JSON.stringify(req)) })).json();
  const CORP = /\(주\)|㈜|주식회사|유한회사|\(유\)|株式会社|有限公司|[,.\s]+(co\.?,?\s*ltd|corporation|corp|inc|ltd|limited|private|llc|pte|gmbh|company)\.?(?=[\s,.]|$)/gi;
  const core = v => { let s = v || ''; for (let i = 0; i < 4; i++) { const w = s.replace(CORP, ' ').replace(/\s+/g, ' ').replace(/^[ ,.]+|[ ,.]+$/g, ''); if (w === s) break; s = w; } return s; };
  const nz = v => (v || '').toLowerCase().replace(/[\s.,()·\-_'&]/g, '');
  const cores = new Set(names.map(n => nz(core(n))).filter(x => x.length >= 2));
  /* 이름이 얼마나 맞나: 0 = 알맹이가 같음 · 1 = 한쪽이 다른 쪽으로 시작(넥슨 ↔ 넥슨코리아, 3자 이상) · 2 = 그 밖 (추천에 딸려 나온 남의 회사일 수 있음) */
  const sim = (name, set) => { const c = nz(core(name)); if (!c) return 2; if (set.has(c)) return 0;
    return [...set].some(k => (k.length >= 3 && c.startsWith(k)) || (c.length >= 3 && k.startsWith(c))) ? 1 : 2; };
  const cand = new Map(), doms = new Set(domains), asked = new Set(), log = { queries: [], domains: [] };
  const add = (id, name, region, from, rank) => { if (!id) return; const o = cand.get(id); if (!o) cand.set(id, { name: (name || '').trim(), region: region || '', from, rank }); else if (rank < o.rank) { o.rank = rank; o.from = from; } };
  const suggest = async (q, label, mode, set) => {   // mode: 'all' 광고주+도메인 · 'dom' 도메인만 · 'kr' 한국 광고주만
    const key = nz(q); if (!key || asked.has(key)) return; asked.add(key);
    let sg; try { sg = await rpc('SearchService/SearchSuggestions', { 1: q, 2: 10, 3: 10 }); } catch (e) { log.queries.push({ q, error: String(e).slice(0, 80) }); return; }
    let na = 0, nd = 0;
    for (const x of sg[1] || []) {
      if (x[1]) { if (mode === 'dom' || (mode === 'kr' && x[1][3] && x[1][3] !== 'KR')) continue;
        const s = sim(x[1][1], set); if (s === 2 && x[1][3] !== 'KR') continue;   // 이름도 다르고 한국 계정도 아니면 버림 (예 'Ryong' → 'Jae Ryong Kim')
        add(x[1][2], x[1][1], x[1][3], `${label} '${q}'`, mode === 'kr' ? 3 + s : s === 2 ? 6 : s === 1 ? 2 : 0); na++; }
      else if (x[2]?.[1] && mode !== 'kr') { const d = x[2][1], lb = nz(d.split('.')[0]); if (lb === key || cores.has(lb)) { doms.add(d); nd++; } }   // 이름과 같은 도메인만 (levelinfinite.com · cookapps.com)
    }
    log.queries.push({ q, adv: na, dom: nd });
  };
  for (const n of names) await suggest(n, '이름', 'all', cores);
  for (const h of hints) await suggest(h, '패키지', 'dom', cores);
  const via = new Map();   // 도메인으로 광고하는 광고주 이름 → 도메인
  for (const d of doms) {
    let r; try { r = await rpc('SearchService/SearchCreatives', { 2: 100, 3: { 12: { 1: d, 2: true }, 8: [2410] }, 7: { 1: 1, 2: 0, 3: 2410 } }); } catch (e) { log.domains.push({ d, error: String(e).slice(0, 80) }); continue; }
    const s = new Map(), tot = (r[1] || []).length;
    (r[1] || []).forEach(c => { s.set(c[12], (s.get(c[12]) || 0) + 1); add(c[1], c[12], '', `도메인 '${d}'`, sim(c[12], cores) === 0 ? 0 : 1); });   // 회사 도메인으로 실제 광고 중인 계정 = 강한 근거 (이름이 달라도 — 한글 법인명 · 대행사 · 해외 법인)
    /* 이 도메인 소재의 30% 이상을 낸 광고주 2곳까지만 '같은 회사'로 보고 이름을 다시 검색 (소재 몇 개만 낸 대행사 — 예 KT 나스미디어 — 의 계정 10여 개가 딸려 오지 않게) */
    [...s.entries()].sort((x, y) => y[1] - x[1]).slice(0, 2).forEach(([n, k]) => { if (k >= tot * .3 && !via.has(n)) via.set(n, d); });
    log.domains.push({ d, creatives: tot, adv: s.size });
  }
  /* 도메인에서 나온 광고주 이름(예 '(주) 컴투스')의 알맹이로 다시 이름 검색 → 같은 회사의 다른 계정까지 (한국 계정만) */
  for (const n of via.keys()) await suggest(core(n), '도메인→이름', 'kr', new Set([nz(core(n))]));
  /* 이름 · 도메인 조회가 전부 실패했으면 차단(429)된 것 → 오류로 돌려보내 다음 추적 때 다시 찾게 함 */
  const tried = log.queries.length + log.domains.length, failed = log.queries.filter(q => q.error).length + log.domains.filter(d => d.error).length;
  if (tried && failed === tried) return { matched: [], checked: [], queries: log.queries, domainLog: log.domains, candidates: 0, error: '광고 투명성 센터 조회가 모두 실패 (일시 차단일 수 있음)' };
  const order = [...cand.entries()].sort((x, y) => x[1].rank - y[1].rank || (y[1].region === 'KR') - (x[1].region === 'KR')).slice(0, 40);
  const out = { matched: [], checked: [], queries: log.queries, domainLog: log.domains, candidates: cand.size }; let budget = 1000, live = 0;
  const T0 = Date.now(), LIMIT = 180000;   // 게임 하나에 3분 · 미리보기 1000건까지 (11/13: 10분 동안 게임 5개 · 미리보기 2000건 안팎을 몰아 조회하면 차단됨)
  /* 미리보기가 너무 많으면 구글이 막으므로(429) 게임당 1000건 · 10건씩 · 3분. 1차는 계정마다 300건까지(계정이 여러 개인 회사도 고루 확인),
     하나도 못 찾았으면 2차로 앞 순위 계정을 남은 한도만큼 더 깊이 */
  const st = [];
  const scan = async (o, cap) => {
    let n = 0;
    for (; o.pos < o.all.length && !o.hit && budget > 0 && n < cap && Date.now() - T0 < LIMIT; o.pos += 10) {
      const res = await Promise.all(o.all.slice(o.pos, o.pos + 10).map(async c => { const u = c[3]?.[1]?.[4]; if (!u) return 0; o.seen++; budget--; n++;
        try { const r = await fetch(u); if (!r.ok) { o.fail++; return 0; } const t = await r.text(); return ids.some(x => t.includes(x)) ? 1 : 0; } catch (e) { o.fail++; return 0; } }));
      o.hit += res.reduce((s, v) => s + v, 0);
      await new Promise(r => setTimeout(r, 120));
    }
  };
  for (const [id, a] of order) {
    const o = { id, a, all: [], total: 0, pos: 0, seen: 0, fail: 0, hit: 0 }; st.push(o);
    if (budget <= 0 || live >= 16 || Date.now() - T0 >= LIMIT) { o.skipped = true; continue; }   // 소재가 있는 계정 16개까지만 확인 (빈 계정은 세지 않음)
    let tok = null;
    for (let p = 0; p < (a.rank <= 1 ? 15 : 5); p++) {   // 앞 순위 계정은 소재 1500개까지(예 Proxima Beta), 그 밖은 500개까지 (최근 노출순 · 요청 수를 줄여 차단을 피함)
      const req = { 2: 100, 3: { 12: { 1: '', 2: true }, 13: { 1: [id] }, 8: [2410] }, 7: { 1: 1, 2: 0, 3: 2410 } }; if (tok) req[4] = tok;
      let j; try { j = await rpc('SearchService/SearchCreatives', req); } catch (e) { break; }
      o.all = o.all.concat(j[1] || []); tok = j[2]; if (!tok) break;
    }
    o.total = o.all.length; o.all = o.all.filter(c => +(c[7]?.[1] || 0) >= since); if (o.all.length) live++;
    await scan(o, 300);
  }
  if (!st.some(o => o.hit)) for (const o of st) { if (o.a.rank <= 3 && Date.now() - T0 < LIMIT) await scan(o, 1e9); if (o.hit) break; }
  for (const o of st) {
    out.checked.push({ id: o.id, name: o.a.name, region: o.a.region, from: o.a.from, rank: o.a.rank, creativesKR: o.total, recent: o.all.length, previews: o.seen, failed: o.fail, hit: o.hit > 0, ...(o.skipped ? { skipped: true } : {}) });
    if (o.hit) out.matched.push({ id: o.id, name: o.a.name, by: `${o.a.from} → 소재에 패키지/iOS ID` });
  }
  return out;
}

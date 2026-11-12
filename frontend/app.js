const $ = s => document.querySelector(s), css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const hm = s => { if (s == null) return '–'; const m = Math.round(s / 60); return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m` };
const num = (v, d = 0) => v == null ? '–' : Number(v).toFixed(d);
const day = d => new Date(d + 'T00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
const esc = s => String(s).replace(/[&<>"]/g, c => `&#${c.charCodeAt(0)};`);
const pace = m => { if (!m) return '–'; const s = Math.round(m * 60); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` };
const hhmm = m => `${String(Math.floor(m / 60) % 24).padStart(2, '0')}:${String(Math.round(m % 60)).padStart(2, '0')}`;
const barRow = (label, share, value) => `<div class="zone"><span>${label}</span><div><i style="width:${share}%"></i></div><em>${value} · ${num(share)}%</em></div>`;
const STALE_MS = 36 * 3600 * 1000, WORKOUT_PAGE = 25;
const PRESET_TAGS = ['alcohol', 'late meal', 'late caffeine', 'illness', 'travel'];
const todayIso = () => new Date().toLocaleDateString('en-CA');  // YYYY-MM-DD, local
let charts = [], popCharts = [], popMap = null, DAILY = [], ACTS = [], TAGS = { days: {}, known: [] }, workoutTab = 'all', workoutsShown = WORKOUT_PAGE, tagDay = todayIso();

// On touch screens a chart's tooltip stays up after a tap and can cover the chart: a tap anywhere else, or on the same day again, hides it
let tipTap = null;
const hideChartTip = c => { c.tooltip.setActiveElements([], { x: 0, y: 0 }); c.setActiveElements([]) };
document.addEventListener('pointerdown', e => {
  tipTap = null;
  for (const c of Object.values(Chart.instances)) {
    const act = c.tooltip?.getActiveElements() || [];
    if (!act.length) continue;
    if (e.target === c.canvas) tipTap = { c, i: act[0].index };
    else { hideChartTip(c); c.update('none') }
  }
}, true);
Chart.register({ id: 'tapToHideTip', afterEvent(c, args) {
  if (args.event.type !== 'click' || tipTap?.c !== c) return;
  const act = c.tooltip.getActiveElements();
  if (act.length && act[0].index === tipTap.i) { hideChartTip(c); args.changed = true }
  tipTap = null;
} });
const viewNow = () => location.hash === '#workouts' ? 'workouts' : 'graphs';  // in the URL, so a reload keeps the view

async function api(p, o) { const r = await fetch(p, o); if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m } catch { } throw new Error(m) } return r.json() }
function say(t) { const m = $('#msg'); m.hidden = !t; m.textContent = t || '' }

async function load() {
    const days = +$('#range').value;
    [DAILY, ACTS, TAGS] = await Promise.all([api('/api/daily?days=' + days), api('/api/activities'), api('/api/tags')]);
    const st = await api('/api/status');
    const okAt = st && st.last_ok_at && new Date(st.last_ok_at), okText = okAt && okAt.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
    $('#synced').textContent = okAt ? 'Synced ' + okText : 'Not synced yet';
    const notes = [];
    if (okAt && Date.now() - okAt > STALE_MS) notes.push(`Data is out of date: the last successful sync was ${okText}.`);
    if (st && st.errors.length) notes.push('Some data could not be synced: ' + st.errors.join(' | '));
    say(notes.join(' '));
    render();
}

const typeKey = a => a.type || 'other';
// Garmin's primary benefit keys, worded as on the watch where known; unknown keys fall back to title case.
const BENEFITS = { aerobic_base: 'Base', lactate_threshold: 'Threshold', vo2max: 'VO2 max', anaerobic_capacity: 'Anaerobic' };
const benefit = k => k ? BENEFITS[k] || typeLabel(k) : '';
const BENEFIT_HELP = "Garmin's label for the main training effect of a run, estimated from your heart rate and intensity against your own fitness. "
    + 'Recovery: very easy, helps you recover. Base: easy aerobic running, builds endurance. Tempo: steady, moderately hard, raises the pace you can hold. '
    + 'Threshold: hard sustained effort, raises your lactate threshold. VO2 max: hard intervals, raises your maximum aerobic capacity. '
    + 'Anaerobic / Sprint (Speed): short, near all-out efforts, build speed and power.';
const info = text => `<span class="info" tabindex="0" role="img" data-tip="${esc(text)}" aria-label="${esc(text)}">?</span>`;
// One tooltip element, fixed so the table's scroll area can't clip it, moved into the dialog when the icon is in the pop-up
// (a modal dialog sits above everything else on the page).
const tip = Object.assign(document.createElement('div'), { id: 'tip', hidden: true });
tip.setAttribute('aria-hidden', 'true');
function showTip(icon) {
    (icon.closest('dialog') || document.body).append(tip);
    tip.textContent = icon.dataset.tip; tip.hidden = false; tip.anchor = icon;
    const r = icon.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = Math.max(8, Math.min(r.left + r.width / 2 - w / 2, innerWidth - w - 8)) + 'px';
    tip.style.top = (r.bottom + 6 + h > innerHeight ? r.top - 6 - h : r.bottom + 6) + 'px';
}
const hideTip = () => { tip.hidden = true; tip.anchor = null };
document.addEventListener('mouseover', e => { const i = e.target.closest?.('.info'); if (i) showTip(i); else if (tip.anchor) hideTip() });
document.addEventListener('focusin', e => { const i = e.target.closest?.('.info'); if (i) showTip(i) });
document.addEventListener('focusout', e => { if (e.target === tip.anchor) hideTip() });
document.addEventListener('click', e => {  // tap to show on touch screens, tap elsewhere to hide
    const i = e.target.closest?.('.info');
    if (i) { e.stopPropagation(); showTip(i) } else hideTip();
}, true);
addEventListener('scroll', hideTip, true);
const RECOVERY_HELP = "Garmin's recovery time when the run ended, from an undocumented field of the activity file. It includes recovery left from earlier activities and counts down afterwards.";
const VO_HELP = 'Vertical oscillation: how far your torso moves up and down with each step. Less bounce usually means less wasted energy.';
const typeLabel = t => { if (t === 'hiit') return 'HIIT'; const s = t.replaceAll('_', ' '); return s[0].toUpperCase() + s.slice(1) };

// The Workouts view lists every stored workout, whatever the range, grouped by month like a training log.
const dayY = d => d.slice(0, 4) === todayIso().slice(0, 4) ? day(d) : new Date(d + 'T00:00').toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
const hasDistance = t => /run|walk|hik|cycl|bik|swim|row|ski|paddl/.test(t);  // squash or HIIT pick up GPS drift, not distance
const ACCENTS = { running: '--steps', trail_running: '--steps', treadmill_running: '--steps', hiit: '--heart', squash: '--deep', strength_training: '--awake', walking: '--light', cycling: '--rem' };
const accent = a => ACCENTS[typeKey(a)] || '--hrv';
function whenLabel(d) {  // like a phone's log: Today, Yesterday, weekday within the week, then the date
    const ago = Math.round((new Date(todayIso() + 'T00:00') - new Date(d + 'T00:00')) / 864e5);
    if (ago === 0) return 'Today';
    if (ago === 1) return 'Yesterday';
    return ago < 7 ? new Date(d + 'T00:00').toLocaleDateString(undefined, { weekday: 'long' }) : dayY(d);
}

function monthSummary(items, active) {
    const n = items.length, sum = k => items.reduce((t, a) => t + (a[k] || 0), 0);
    const time = sum('duration_s'), dist = items.filter(a => hasDistance(typeKey(a))), km = dist.reduce((t, a) => t + (a.distance_m || 0), 0) / 1000;
    const runs = /run/.test(active), kcal = sum('calories'), load = sum('training_load');
    const big = v => Math.round(v).toLocaleString();
    const row = (label, total, avg) => `<tr><th>${label}</th><td>${total}</td><td>${avg ?? ''}</td></tr>`;
    const runKm = runs && km ? dist.reduce((t, a) => t + (a.duration_s || 0), 0) / 60 / km : null;
    return `<table class="month-sum"><thead><tr><th></th><th>Total</th><th>Average</th></tr></thead><tbody>
    ${row(runs ? 'Runs' : 'Workouts', n)}${row('Time', hm(time), hm(time / n))}
    ${km >= 1 ? row('Distance', num(km, 1) + ' km', num(km / dist.length, 1) + ' km') : ''}${runKm ? row('Pace', '', pace(runKm) + ' /km') : ''}
    ${kcal ? row('Calories', big(kcal) + ' kcal', big(kcal / n) + ' kcal') : ''}${load ? row('Training load', big(load), big(load / n)) : ''}</tbody></table>`;
}

function workoutCard(a) {
    const t = typeKey(a), d = a.start_local.slice(0, 10), km = hasDistance(t) && a.distance_m > 100 ? a.distance_m / 1000 : null;
    const big = km ? `${num(km, 2)}<small>km</small>` : esc(hm(a.duration_s));
    const bits = [km && hm(a.duration_s), km && a.duration_s && pace(a.duration_s / 60 / km) + ' /km', a.avg_hr && num(a.avg_hr) + ' bpm', a.calories && Math.round(a.calories).toLocaleString() + ' kcal',
        a.run?.primary_benefit && esc(benefit(a.run.primary_benefit)), a.training_load != null && 'Load ' + num(a.training_load)].filter(Boolean);
    const inner = `<span class="w-title">${esc(a.name || typeLabel(t))}</span><span class="w-when">${whenLabel(d)}</span>
      <b style="color:var(${accent(a)})">${big}</b><span class="w-bits">${bits.join(' · ')}</span>`;
    return a.run ? `<button class="wcard" data-run="${a.id}" aria-label="${esc(a.name || typeLabel(t))}, ${day(d)}: open details">${inner}<i aria-hidden="true">›</i></button>`
        : `<div class="wcard">${inner}</div>`;
}

function workoutsHtml() {
    const counts = {};
    ACTS.forEach(a => { counts[typeKey(a)] = (counts[typeKey(a)] || 0) + 1 });
    const types = Object.keys(counts).sort((x, y) => counts[y] - counts[x] || x.localeCompare(y));
    const active = counts[workoutTab] ? workoutTab : 'all';
    const tab = (key, label, n) => `<button data-tab="${esc(key)}" aria-pressed="${key === active}">${esc(label)} <span>${n}</span></button>`;
    const tabs = types.length > 1 ? `<div class="chips" role="group" aria-label="Workout type">${tab('all', 'All', ACTS.length)}${types.map(t => tab(t, typeLabel(t), counts[t])).join('')}</div>` : '';
    const list = active === 'all' ? ACTS : ACTS.filter(a => typeKey(a) === active), shown = list.slice(0, workoutsShown);
    if (!list.length) return `${tabs}<p class="empty">No workouts synced yet.</p>`;
    const months = [...new Set(shown.map(a => a.start_local.slice(0, 7)))].map(m => {
        const all = list.filter(a => a.start_local.startsWith(m));  // the summary covers the whole month, even before Show more
        const name = new Date(m + '-01T00:00').toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
        return `<div class="month"><h3>${name}</h3>${monthSummary(all, active)}<div class="wcards">${shown.filter(a => a.start_local.startsWith(m)).map(workoutCard).join('')}</div></div>`;
    }).join('');
    const more = list.length > shown.length ? `<p class="more"><button data-more>Show ${Math.min(WORKOUT_PAGE, list.length - shown.length)} more</button> <span>${shown.length} of ${list.length}</span></p>` : '';
    return `${tabs}${months}${more}`;
}

function tagsHtml() {
    return `<h3>Tags</h3><p>Things the watch can't see, logged on the day they happened. Sleep is dated by the morning you wake up, so last night's alcohol goes on yesterday. Tags go into the export: leave out names and places.</p>
  <div class="tagbar"><input type="date" id="tag-day" value="${tagDay}" max="${todayIso()}" aria-label="Day to tag"></div><div id="tag-body">${tagBodyHtml()}</div>`;
}

function tagBodyHtml() {  // everything below the date input, so changing the date doesn't reset the input
    const mine = TAGS.days[tagDay] || [];
    const chips = mine.map(t => `<span class="chip">${esc(t)}<button data-untag="${esc(t)}" aria-label="Remove ${esc(t)}">×</button></span>`).join('') || '<span class="none">No tags for this day.</span>';
    const quick = [...new Set([...TAGS.known, ...PRESET_TAGS])].filter(t => !mine.includes(t)).slice(0, 12).map(t => `<button class="quick" data-addtag="${esc(t)}">+ ${esc(t)}</button>`).join('');
    const tagged = Object.keys(TAGS.days).filter(d => d >= DAILY[0].date).sort().reverse().map(d => `<li><button class="link" data-tagday="${d}">${day(d)}</button> ${TAGS.days[d].map(esc).join(', ')}</li>`).join('');
    return `<div class="tagbar">${chips}</div>
  <form id="tag-form" class="tagbar"><input id="tag-new" list="tag-known" maxlength="40" placeholder="New tag" aria-label="New tag"><button>Add</button><datalist id="tag-known">${TAGS.known.map(t => `<option value="${esc(t)}">`).join('')}</datalist>${quick}</form>
  ${tagged ? `<h4>Tagged days since ${day(DAILY[0].date)}</h4><ul class="tagged">${tagged}</ul>` : ''}`;
}

async function saveTags(list, refocus) {
    TAGS = await api('/api/tags/' + tagDay, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tags: list }) });
    $('#tag-body').innerHTML = tagBodyHtml();
    if (refocus) $(refocus)?.focus();
}

function render() {
    charts.forEach(c => c.destroy()); charts = [];
    const app = $('#app'), view = viewNow();
    document.querySelectorAll('[data-view]').forEach(b => b.setAttribute('aria-pressed', b.dataset.view === view));
    ['#range', '#export', '#sync'].forEach(s => { $(s).hidden = view === 'workouts' });  // graphs-only controls; the header keeps its height
    if (view === 'workouts') { app.innerHTML = `<section id="workouts">${workoutsHtml()}</section>`; return }
    if (!DAILY.length) { app.innerHTML = '<div class="empty">No data yet. Select <b>Sync now</b> to load the last 28 days from Garmin.</div>'; return }
    const L = [...DAILY].reverse(), last = k => (L.find(r => r[k] != null) || {})[k];
    const sl = L.find(r => r.sleep_s != null) || {}, tot = (sl.deep_s || 0) + (sl.light_s || 0) + (sl.rem_s || 0) + (sl.awake_s || 0) || 1;
    const seg = (k, c) => `<i style="width:${(sl[k] || 0) / tot * 100}%;background:var(${c})"></i>`;
    const t = L[0], stressDay = t.stress_avg != null ? t.date : (L.find(r => r.stress_avg != null) || {}).date;
    app.innerHTML = `
  <div class="hero"><h2>Sleep, night ending ${sl.date ? day(sl.date) : '–'}</h2>
    <div class="big"><b>${hm(sl.sleep_s)}</b>${sl.sleep_score != null ? `<button class="link" data-sleep="${sl.date}">Garmin sleep score ${num(sl.sleep_score)}</button>` : `<span>Garmin sleep score –</span>`}</div>
    <div class="stages" role="img" aria-label="Sleep stages">${seg('deep_s', '--deep')}${seg('light_s', '--light')}${seg('rem_s', '--rem')}${seg('awake_s', '--awake')}</div>
    <div class="legend"><span><u style="background:var(--deep)"></u>Deep ${hm(sl.deep_s)}</span><span><u style="background:var(--light)"></u>Light ${hm(sl.light_s)}</span><span><u style="background:var(--rem)"></u>REM ${hm(sl.rem_s)}</span><span><u style="background:var(--awake)"></u>Awake ${hm(sl.awake_s)}</span></div></div>
  <div class="stats">
    <div class="stat"><small>Resting heart rate</small><b>${num(last('rhr'))}</b><em>bpm</em></div>
    <div class="stat"><small>HRV last night</small><b>${num(last('hrv_last_night'))}</b><em>ms${last('hrv_status') ? ' · ' + sentence(last('hrv_status')) : ''}</em></div>
    <div class="stat"><small>Steps ${day(t.date)}</small><b>${num(t.steps).replace(/\B(?=(\d{3})+$)/g, ',')}</b><em>/ ${num(t.step_goal)}</em></div>
    <div class="stat"${stressDay ? ` data-stress="${stressDay}"` : ''}>${stressDay ? '<button class="link"><small>Average stress</small></button>' : '<small>Average stress</small>'}<b>${num(t.stress_avg ?? last('stress_avg'))}</b><em>/ 100</em></div>
    <div class="stat"><small>Body battery now</small><b>${num(t.bb_latest ?? last('bb_latest'))}</b><em>${num(t.bb_min)}–${num(t.bb_max)} today</em></div>
  </div>
  <section id="tags">${tagsHtml()}</section>
  <section><h3>Sleep</h3><p>Hours per stage, and Garmin's overall score.</p><div class="chart"><canvas id="c1"></canvas></div></section>
  <section><h3>Resting heart rate and HRV</h3><p>Resting heart rate (left axis) and HRV (right axis). The shaded band is Garmin's balanced HRV range, worked out from your own recent weeks; it's missing for nights before Garmin had enough data. Garmin's HRV status compares the solid 7-day average with the band, not single nights (dotted), so one low night can sit below the band while the status stays balanced.</p><div class="chart"><canvas id="c2"></canvas></div></section>
  <section><h3>Body battery</h3><p>How much body battery you gained and used each day.</p><div class="chart"><canvas id="c6"></canvas></div></section>
  <section><h3>Training load</h3><p>Garmin's training load per day, summed over workouts.</p><div class="chart"><canvas id="c5"></canvas></div></section>`;

    Chart.defaults.color = css('--mute'); Chart.defaults.borderColor = css('--line'); Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Object.assign(Chart.defaults.scales.category.ticks, { maxRotation: 0, autoSkipPadding: 12 });  // skip date labels on narrow screens instead of slanting them
    const lab = DAILY.map(r => day(r.date)), h = k => DAILY.map(r => r[k] == null ? null : r[k] / 3600);
    const base = { maintainAspectRatio: false, interaction: { mode: 'index', intersect: false }, plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, usePointStyle: true, sort: (a, b) => a.datasetIndex - b.datasetIndex, filter: (i, d) => { const ds = d.datasets[i.datasetIndex]; if (ds.legendText) i.text = ds.legendText; return !ds.noLegend } } }, tooltip: { itemSort: (a, b) => a.datasetIndex - b.datasetIndex } } };
    const mk = (id, type, datasets, scales) => charts.push(new Chart($('#' + id), { type, data: { labels: lab, datasets }, options: { ...base, scales } }));
    const ln = (label, k, c, extra = {}) => ({ type: 'line', label, data: DAILY.map(r => r[k]), borderColor: css(c), backgroundColor: css(c), tension: .25, pointRadius: 2, spanGaps: true, order: -1, ...extra });  // order: drawn over the bars
    const bar = (label, data, c, extra = {}) => ({ type: 'bar', label, data, backgroundColor: css(c), ...extra });
    mk('c1', 'bar', [bar('Deep', h('deep_s'), '--deep', { stack: 's' }), bar('Light', h('light_s'), '--light', { stack: 's' }), bar('REM', h('rem_s'), '--rem', { stack: 's' }), bar('Awake', h('awake_s'), '--awake', { stack: 's' }), ln('Sleep score', 'sleep_score', '--ink', { yAxisID: 'y1' })],
        { x: { stacked: true }, y: { stacked: true, title: { display: true, text: 'hours' } }, y1: { position: 'right', min: 0, max: 100, grid: { drawOnChartArea: false }, title: { display: true, text: 'score' } } });
    const band = { yAxisID: 'y1', pointRadius: 0, borderWidth: 0, tension: 0, spanGaps: false, order: 1, backgroundColor: css('--hrv') + '2e' };
    mk('c2', 'line', [ln('Resting HR (bpm)', 'rhr', '--heart'), ln('HRV 7-day average (ms)', 'hrv_weekly', '--hrv', { yAxisID: 'y1', borderWidth: 3 }),
        ln('HRV night (ms)', 'hrv_last_night', '--hrv', { yAxisID: 'y1', borderWidth: 1.5, borderDash: [4, 3], borderColor: css('--hrv') + '99', backgroundColor: css('--hrv') + '99', pointRadius: 1.5 }),
        ln('HRV balanced low (ms)', 'hrv_baseline_low', '--hrv', { ...band, fill: '+1', legendText: 'HRV balanced range' }), ln('HRV balanced high (ms)', 'hrv_baseline_high', '--hrv', { ...band, noLegend: true })], { y: { title: { display: true, text: 'bpm' } }, y1: { position: 'right', grid: { drawOnChartArea: false }, title: { display: true, text: 'ms' } } });
    mk('c6', 'bar', [bar('Battery gained', DAILY.map(r => r.bb_charged), '--steps'), bar('Battery used', DAILY.map(r => r.bb_drained), '--awake')], { y: { beginAtZero: true, title: { display: true, text: 'body battery' } } });
    const load = {}; ACTS.forEach(a => { const d = a.start_local.slice(0, 10); load[d] = (load[d] || 0) + (a.training_load || 0) });
    mk('c5', 'bar', [bar('Training load', DAILY.map(r => load[r.date] ?? 0), '--deep')], { y: { beginAtZero: true } });
}

function zoneRows(r) {
    const z = r.hr_zone_minutes, b = r.hr_zone_bounds_bpm;
    if (!z) return '';
    const total = Object.values(z).reduce((x, y) => x + y, 0) || 1;
    const rows = [1, 2, 3, 4, 5].map(n => {
        const bpm = b ? ` ${b[n - 1]}–${n === 5 ? b[5] : b[n] - 1}` : '', share = z['z' + n] / total * 100;
        return barRow(`Z${n}${bpm}`, share, `${num(z['z' + n], 1)} min`);
    }).join('');
    return `<section><h3>Time in each zone</h3><p>Heart rate zones in bpm, as share of the time spent in zones. Time below zone 1 isn't counted.</p>${rows}</section>`;
}

function splitRows(r) {
    const S = r.splits;
    if (!S) return '<section><h3>Splits</h3><p>No split data for this run.</p></section>';
    const graphs = S.length > 1 ? `<div class="pair"><div><h4>Pace (min/km)</h4><div class="chart"><canvas id="run-pace"></canvas></div></div><div><h4>Average heart rate (bpm)</h4><div class="chart"><canvas id="run-hr"></canvas></div></div></div>` : '';
    const body = S.map(s => `<tr><td>${s.km}</td><td>${pace(s.pace_min_per_km)}</td><td>${num(s.avg_hr)}</td><td>${num(s.elevation_gain_m)}</td><td>${num(s.elevation_loss_m)}</td><td>${num(s.cadence_spm)}</td></tr>`).join('');
    return `<section><h3>${r.split_type === 'km' ? 'Km splits' : 'Laps'}</h3><p>Km is where each split ends. Elevation comes from the barometer and isn't corrected.</p>${graphs}<div class="wrap"><table><thead><tr><th>Km</th><th>Pace</th><th>Avg HR</th><th>Gain (m)</th><th>Loss (m)</th><th>Cadence</th></tr></thead><tbody>${body}</tbody></table></div></section>`;
}

function showPopup(title, sub, body) {
    const dlg = $('#pop');
    popCharts.forEach(c => c.destroy()); popCharts = [];  // when switching nights without closing
    dlg.innerHTML = `<div class="pop">
  <header><div><h2 id="pop-title">${title}</h2><span class="bar">${sub}</span></div><button autofocus onclick="$('#pop').close()">Close</button></header>
  ${body}</div>`;
    if (!dlg.open) { pinPage(true); dlg.showModal() }
    // The one dialog is reused, so it would keep the last pop-up's scroll position; iOS can restore it after the
    // first reset, hence once more after the next frame.
    dlg.scrollTop = 0; requestAnimationFrame(() => { dlg.scrollTop = 0 });
}

// While a pop-up is open the page behind it is pinned in place, so only the pop-up scrolls. iOS ignores
// overflow: hidden for touch scrolling, so the body is fixed at its current offset and put back on close.
function pinPage(on) {
    const b = document.body;
    if (on && !b.style.position) { b.dataset.y = scrollY; Object.assign(b.style, { position: 'fixed', top: -scrollY + 'px', left: '0', right: '0' }) }
    if (!on && b.style.position) { Object.assign(b.style, { position: '', top: '', left: '', right: '' }); scrollTo(0, +b.dataset.y) }
}

const STRESS_BANDS = [['Rest', 'stress_rest_s'], ['Low', 'stress_low_s'], ['Medium', 'stress_med_s'], ['High', 'stress_high_s'], ['Activity', 'stress_activity_s'], ['Uncategorized', 'stress_uncat_s']];
const EVENT_COLORS = { SLEEP: '--deep', ACTIVITY: '--awake', STRESS: '--heart', RECOVERY: '--hrv' };

async function openStress(date) {
    const r = DAILY.find(d => d.date === date), curve = await api('/api/body-battery/' + date);
    const partial = !r.summary_fetched_on || r.summary_fetched_on <= r.date;
    const tiles = [['Average', r.stress_avg], ['Max', r.stress_max], ['During sleep', r.sleep_stress]].filter(t => t[1] != null).map(([k, v]) => `<div><small>${k}</small><b>${num(v)}</b> / 100</div>`).join('');
    const total = STRESS_BANDS.reduce((sum, [, k]) => sum + (r[k] || 0), 0);
    const bands = total ? STRESS_BANDS.filter(([, k]) => r[k]).map(([label, k]) => barRow(label, r[k] / total * 100, hm(r[k]))).join('') : '<p>No band data stored for this day yet. Select <b>Sync now</b> to load it.</p>';
    const legend = curve.events.map(e => `<span><u style="background:var(${EVENT_COLORS[e.type] || '--mute'})"></u>${esc(e.type[0] + e.type.slice(1).toLowerCase())} ${hhmm(e.start_min)}–${hhmm(e.end_min)}</span>`).join('');
    const graph = curve.points.length ? `<div class="chart"><canvas id="pop-bb"></canvas></div><div class="legend">${legend}</div>` : '<p>No curve stored for this day yet. Select <b>Sync now</b> to load it.</p>';
    showPopup('Stress', `${day(date)}${partial ? ', day not finished: totals so far' : ''}`, `<div class="tiles">${tiles}</div>
  <section><h3>Time in each stress band</h3><p>Garmin's estimate from the watch, as share of the time it recorded.</p>${bands}</section>
  <section><h3>Body battery through the day</h3><p>This is body battery, not stress: Garmin's Personal API has no stress readings through the day. Shaded periods are the ones Garmin flagged, including stress periods when there are any.</p>${graph}</section>`);
    if (!$('#pop-bb')) return;
    const shade = {
        id: 'events', beforeDatasetsDraw(c) {
            const { ctx, chartArea: a, scales: { x } } = c;
            ctx.save(); ctx.globalAlpha = .18;
            curve.events.forEach(e => { ctx.fillStyle = css(EVENT_COLORS[e.type] || '--mute'); ctx.fillRect(x.getPixelForValue(e.start_min), a.top, x.getPixelForValue(e.end_min) - x.getPixelForValue(e.start_min), a.bottom - a.top) });
            ctx.restore();
        }
    };
    popCharts.push(new Chart($('#pop-bb'), {
        type: 'line', plugins: [shade],
        data: { datasets: [{ data: curve.points.map(([x, y]) => ({ x, y })), borderColor: css('--steps'), backgroundColor: css('--steps'), borderWidth: 2, pointRadius: 0, pointHitRadius: 10 }] },
        options: {
            maintainAspectRatio: false, interaction: { mode: 'nearest', axis: 'x', intersect: false },
            plugins: { legend: { display: false }, tooltip: { callbacks: { title: i => hhmm(i[0].parsed.x), label: c => 'Body battery ' + c.parsed.y } } },
            scales: { x: { type: 'linear', min: 0, max: Math.max(1440, curve.points.at(-1)[0]), ticks: { stepSize: 180, callback: hhmm } }, y: { min: 0, max: 100 } }
        }
    }));
}

const SLEEP_FACTORS = [['totalDuration', 'Duration'], ['stress', 'Stress'], ['awakeCount', 'Times awake'], ['restlessness', 'Restlessness'], ['deepPercentage', 'Deep sleep'], ['lightPercentage', 'Light sleep'], ['remPercentage', 'REM sleep']];
const sentence = s => s[0].toUpperCase() + s.slice(1).toLowerCase().replaceAll('_', ' ');

async function openSleep(date) {
    const r = DAILY.find(d => d.date === date), d = JSON.parse(r.sleep_details || '{}'), S = d.scores || {};
    const { readings } = await api('/api/hrv/' + date);
    const stage = (k, secs) => S[k]?.value != null ? `${S[k].value}% · ${hm(secs)}` : hm(secs);
    const yours = { totalDuration: hm(r.sleep_s), stress: num(r.sleep_stress), awakeCount: num(d.awake_count), restlessness: '–', deepPercentage: stage('deepPercentage', r.deep_s), lightPercentage: stage('lightPercentage', r.light_s), remPercentage: stage('remPercentage', r.rem_s) };
    const optimal = (k, f) => {
        if (k === 'restlessness' || f.optimalStart == null) return '–';  // restlessness has a range but no value to compare with
        if (k === 'totalDuration') return f.optimalStart === f.optimalEnd ? hm(f.optimalStart) : `${hm(f.optimalStart)}–${hm(f.optimalEnd)}`;
        return `${f.optimalStart}–${f.optimalEnd}${k.endsWith('Percentage') ? '%' : ''}`;
    };
    const rows = SLEEP_FACTORS.filter(([k]) => S[k]).map(([k, label]) => {
        const q = S[k].qualifierKey;
        return `<tr><td>${label}</td><td>${yours[k]}</td><td>${optimal(k, S[k])}</td><td${q === 'FAIR' || q === 'POOR' ? ' class="low"' : ''}>${q ? sentence(q) : '–'}</td></tr>`;
    }).join('');
    const score = `${num(r.sleep_score)}${S.overall?.qualifierKey ? ' · ' + sentence(S.overall.qualifierKey) : ''}`;
    const tiles = [['Score', score], ['Sleep start', d.start], ['Wake up', d.end], ['Time asleep', hm(r.sleep_s)], ['Average HR', r.sleep_hr != null && num(r.sleep_hr) + ' bpm'], ['Respiration', d.resp_avg != null && num(d.resp_avg) + ' brpm'], ['SpO2', r.sleep_spo2 != null && num(r.sleep_spo2) + '%']].filter(t => t[1]).map(([k, v]) => `<div><small>${k}</small><b>${v}</b></div>`).join('');
    const notes = [d.feedback, d.insight].filter(n => n && n !== 'NONE').map(n => esc(sentence(n.replace(/^(POSITIVE|NEGATIVE|NEUTRAL)_/, '')))).join('. ');
    const table = rows ? `<div class="wrap"><table><thead><tr><th>Factor</th><th>You</th><th>Optimal</th><th>Rating</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<p>No breakdown stored for this night yet. Select <b>Sync now</b> to load it.</p>';
    const hrvTiles = [['HRV average', r.hrv_last_night != null && num(r.hrv_last_night) + ' ms'], ['Highest 5 min', r.hrv_5min_high != null && num(r.hrv_5min_high) + ' ms'], ['HRV status', r.hrv_status && sentence(r.hrv_status)], ['Balanced range', r.hrv_baseline_low != null && `${num(r.hrv_baseline_low)}–${num(r.hrv_baseline_high)} ms`]].filter(t => t[1]).map(([k, v]) => `<div><small>${k}</small><b>${v}</b></div>`).join('');
    const hrv = readings.length ? `<div class="tiles">${hrvTiles}</div><div class="chart"><canvas id="pop-hrv"></canvas></div>` : `${hrvTiles ? `<div class="tiles">${hrvTiles}</div>` : ''}<p>No readings stored for this night yet. Select <b>Sync now</b> to load them.</p>`;
    const nights = DAILY.filter(n => n.sleep_s != null || n.sleep_score != null).map(n => n.date), i = nights.indexOf(date);
    const step = (to, label, text) => `<button class="step" ${to ? `data-night="${to}"` : 'disabled'} aria-label="${label}">${text}</button>`;
    const nav = `${step(nights[i - 1], 'Previous night', '‹')}Night ending ${day(date)}${step(nights[i + 1], 'Next night', '›')}`;
    showPopup('Sleep score', nav, `<div class="tiles">${tiles}</div>
  <section><h3>What went into the score</h3><p>${notes ? `Garmin's summary: ${notes}. ` : ''}Garmin rates each factor but doesn't say how many points each one adds or removes.</p>${table}</section>
  <section><h3>HRV through the night</h3><p>One reading every 5 minutes while you slept. The dashed line is the night's average. Single readings swing a lot; the average is what Garmin compares with your balanced range.</p>${hrv}</section>`);
    if (!$('#pop-hrv')) return;
    const clock = m => hhmm((m % 1440 + 1440) % 1440);  // minutes before midnight are negative
    const pts = readings.map(([x, y]) => ({ x, y }));
    const avg = r.hrv_last_night != null ? [{ x: pts[0].x, y: r.hrv_last_night }, { x: pts.at(-1).x, y: r.hrv_last_night }] : [];
    popCharts.push(new Chart($('#pop-hrv'), {
        type: 'line',
        data: { datasets: [
            { data: pts, borderColor: css('--hrv'), backgroundColor: css('--hrv'), borderWidth: 2, pointRadius: 0, pointHitRadius: 10, tension: .25 },
            { data: avg, borderColor: css('--mute'), borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0, pointHitRadius: 0 }] },
        options: {
            maintainAspectRatio: false, interaction: { mode: 'nearest', axis: 'x', intersect: false },
            plugins: { legend: { display: false }, tooltip: { filter: i => i.datasetIndex === 0, callbacks: { title: i => clock(i[0].parsed.x), label: c => 'HRV ' + c.parsed.y + ' ms' } } },
            scales: { x: { type: 'linear', min: Math.floor(pts[0].x / 60) * 60, max: Math.ceil(pts.at(-1).x / 60) * 60, ticks: { stepSize: 60, callback: clock } }, y: { title: { display: true, text: 'ms' } } }  // x runs over whole hours so the last label doesn't land next to an hour mark
        }
    }));
}

function drawRoute(points) {
    popMap?.remove();
    popMap = L.map('run-map', { scrollWheelZoom: false });  // the wheel keeps scrolling the pop-up
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }).addTo(popMap);
    // Fixed colours, not theme ones: the tiles look the same in both themes. Violet isn't used by OSM's roads or water.
    L.polyline(points, { color: '#fff', weight: 7, opacity: .9 }).addTo(popMap);
    const route = L.polyline(points, { color: '#6a1be0', weight: 4 }).addTo(popMap);
    const dot = (at, c, label) => L.circleMarker(at, { radius: 6, color: '#fff', weight: 2, fillColor: css(c), fillOpacity: 1 }).bindTooltip(label).addTo(popMap);
    dot(points.at(-1), '--deep', 'Finish');
    dot(points[0], '--steps', 'Start');
    popMap.fitBounds(route.getBounds(), { padding: [16, 16] });
}

async function openRun(a) {
    const { points } = await api(`/api/activities/${a.id}/route`);
    const hasMap = points.length > 1 && window.L;  // no GPS (treadmill), not synced yet, or Leaflet didn't load
    const map = hasMap ? '<section><h3>Route</h3><div id="run-map" class="map"></div></section>' : '';
    const r = a.run, km = a.distance_m / 1000;
    const tiles = [
        ['Distance', a.distance_m && num(km, 2) + ' km'], ['Time', a.duration_s && hm(a.duration_s)],
        ['Average pace', km && a.duration_s && pace(a.duration_s / 60 / km) + ' /km'], ['Grade-adjusted pace', r.avg_gap_min_per_km && pace(r.avg_gap_min_per_km) + ' /km'],
        ['Average HR', a.avg_hr && num(a.avg_hr) + ' bpm'], ['Max HR', a.max_hr && num(a.max_hr) + ' bpm'],
        [r.hr_drift_basis === 'gap' ? 'HR drift' : 'HR drift (not grade-adjusted)', r.hr_drift_pct != null && `${r.hr_drift_pct > 0 ? '+' : ''}${r.hr_drift_pct}%`],
        ['Cadence', r.avg_cadence_spm && r.avg_cadence_spm + ' spm'], ['Stride', r.avg_stride_length_m && r.avg_stride_length_m + ' m'],
        [`Vertical oscillation${info(VO_HELP)}`, r.avg_vertical_oscillation_cm != null && num(r.avg_vertical_oscillation_cm, 1) + ' cm'],
        ['Elevation', r.elevation_gain_m != null && `+${r.elevation_gain_m} / −${r.elevation_loss_m ?? '–'} m`],
        [`Primary benefit${info(BENEFIT_HELP)}`, r.primary_benefit && esc(benefit(r.primary_benefit))],
        ['Aerobic / anaerobic', a.te_aerobic != null && `${num(a.te_aerobic, 1)} / ${num(a.te_anaerobic, 1)}`], ['Training load', a.training_load != null && num(a.training_load)],
        [`Recovery at finish${info(RECOVERY_HELP)}`, r.recovery_hours_at_finish != null && num(r.recovery_hours_at_finish) + ' h'],
    ].filter(t => t[1]).map(([k, v]) => `<div><small>${k}</small><b>${v}</b></div>`).join('');
    showPopup(esc(a.name || a.type), `${day(a.start_local.slice(0, 10))}, ${a.start_local.slice(11, 16)}`, `<div class="tiles">${tiles}</div>${map}${zoneRows(r)}${splitRows(r)}`);
    if (hasMap) drawRoute(points);
    if (!$('#run-pace')) return;
    const opt = (extra = {}) => ({ maintainAspectRatio: false, interaction: { mode: 'index', intersect: false }, plugins: { legend: { display: false }, tooltip: extra.tooltip }, scales: { x: { title: { display: true, text: 'km' } }, y: extra.y || {} } });
    const line = (id, key, c, extra) => popCharts.push(new Chart($('#' + id), { type: 'line', data: { labels: r.splits.map(s => s.km), datasets: [{ data: r.splits.map(s => s[key]), borderColor: css(c), backgroundColor: css(c), borderWidth: 2, pointRadius: 3, tension: .25, spanGaps: true }] }, options: opt(extra) }));
    line('run-pace', 'pace_min_per_km', '--deep', { y: { reverse: true, ticks: { callback: pace } }, tooltip: { callbacks: { label: c => pace(c.parsed.y) + ' /km' } } });
    line('run-hr', 'avg_hr', '--heart', { tooltip: { callbacks: { label: c => c.parsed.y + ' bpm' } } });
}

$('#pop').onclose = () => { pinPage(false); popCharts.forEach(c => c.destroy()); popCharts = []; popMap?.remove(); popMap = null };
$('#pop').onclick = e => {
    if (e.target === e.currentTarget) e.currentTarget.close();
    const night = e.target.closest('[data-night]');
    if (night) openSleep(night.dataset.night).then(() => ($(`#pop [aria-label="${night.getAttribute('aria-label')}"]:not(:disabled)`) || $('#pop [autofocus]')).focus()).catch(err => say(err.message));
};
$('#pop').onkeydown = e => {  // arrow keys step through nights in the sleep pop-up
    const to = { ArrowLeft: 'Previous night', ArrowRight: 'Next night' }[e.key];
    if (to && !e.target.matches('input, select')) $(`#pop [aria-label="${to}"]:not(:disabled)`)?.click();
};
function redrawWorkouts() {  // keeps the type chips' sideways scroll, which a fresh chip row would lose
    const x = $('.chips')?.scrollLeft || 0;
    $('#workouts').innerHTML = workoutsHtml();
    if ($('.chips')) $('.chips').scrollLeft = x;
}

$('#app').onclick = e => {
    const tr = e.target.closest('[data-run]'), s = e.target.closest('[data-stress]'), tab = e.target.closest('[data-tab]');
    if (e.target.closest('[data-more]')) { workoutsShown += WORKOUT_PAGE; redrawWorkouts() }
    if (tab) {
        workoutTab = tab.dataset.tab; workoutsShown = WORKOUT_PAGE;
        redrawWorkouts();
        [...document.querySelectorAll('[data-tab]')].find(b => b.dataset.tab === workoutTab).focus({ preventScroll: true });
    }
    if (tr) openRun(ACTS.find(a => String(a.id) === tr.dataset.run)).catch(err => say(err.message));
    if (s) openStress(s.dataset.stress).catch(err => say(err.message));
    const sleep = e.target.closest('[data-sleep]');
    if (sleep) openSleep(sleep.dataset.sleep).catch(err => say(err.message));
    if (e.target.id === 'tag-day') try { e.target.showPicker() } catch { }  // open the calendar on any click, not just the icon
    const mine = TAGS.days[tagDay] || [], add = e.target.closest('[data-addtag]'), drop = e.target.closest('[data-untag]'), td = e.target.closest('[data-tagday]');
    if (add) saveTags([...mine, add.dataset.addtag], '#tag-new').catch(err => say(err.message));
    if (drop) saveTags(mine.filter(t => t !== drop.dataset.untag), '#tag-new').catch(err => say(err.message));
    if (td) { tagDay = $('#tag-day').value = td.dataset.tagday; $('#tag-body').innerHTML = tagBodyHtml(); $('#tag-day').focus() }
};
$('#app').onchange = e => {
    if (e.target.id !== 'tag-day' || !e.target.value) return;
    tagDay = e.target.value; $('#tag-body').innerHTML = tagBodyHtml();
};
$('#app').onsubmit = e => {
    e.preventDefault();
    const v = $('#tag-new').value.trim();
    if (v) saveTags([...(TAGS.days[tagDay] || []), v], '#tag-new').catch(err => say(err.message));
};
$('#export').onclick = async () => {
    const url = '/api/export?days=' + $('#range').value;
    // On the phone, open the share sheet (e.g. straight into the Claude app); a home-screen web app can't show a plain download.
    if (!matchMedia('(pointer:coarse)').matches || !navigator.canShare) { location.href = url; return }
    try {
        const r = await fetch(url); if (!r.ok) throw new Error(r.statusText);
        const name = (r.headers.get('Content-Disposition') || '').match(/filename="(.+)"/)?.[1] || 'garmin-export.json';
        const file = new File([await r.blob()], name, { type: 'application/json' });
        if (navigator.canShare({ files: [file] })) await navigator.share({ files: [file] }); else location.href = url;
    } catch (err) {
        if (err.name === 'NotAllowedError') location.href = url;  // the tap expired while the export loaded
        else if (err.name !== 'AbortError') say('Export failed: ' + err.message);
    }
};
function showView(v) {
    if (v !== viewNow()) history.replaceState(null, '', v === 'workouts' ? '#workouts' : location.pathname);
    render(); scrollTo(0, 0);
}
document.querySelector('nav.views').onclick = e => { const b = e.target.closest('[data-view]'); if (b) showView(b.dataset.view) };
// Swipe left for Workouts, right for Graphs. Not from charts, the map, sideways-scrolling rows or the screen edges (Safari's back and forward swipes).
{
    let sx = null, sy = 0, st = 0;
    const scrollsSideways = el => { for (; el && el !== document.body; el = el.parentElement) if (el.scrollWidth > el.clientWidth + 1 && /auto|scroll/.test(getComputedStyle(el).overflowX)) return true; return false };
    addEventListener('touchstart', e => {
        const t = e.touches[0], el = e.target;
        sx = e.touches.length === 1 && !$('#pop').open && t.clientX > 24 && t.clientX < innerWidth - 24
            && !el.closest('canvas, .leaflet-container, input, select, textarea') && !scrollsSideways(el) ? t.clientX : null;
        sy = t.clientY; st = Date.now();
    }, { passive: true });
    addEventListener('touchend', e => {
        if (sx == null) return;
        const t = e.changedTouches[0], dx = t.clientX - sx, dy = t.clientY - sy;
        sx = null;
        if (Math.abs(dx) < 70 || Math.abs(dx) < 2 * Math.abs(dy) || Date.now() - st > 700) return;
        const v = dx < 0 ? 'workouts' : 'graphs';
        if (v !== viewNow()) showView(v);
    });
}
$('#range').onchange = () => load().catch(e => say(e.message));
$('#sync').onclick = async e => {
    const b = e.target; b.disabled = true; b.textContent = 'Syncing…'; say('');
    try { const r = await api('/api/sync', { method: 'POST' }); if (r.errors.length) say('Some data could not be synced: ' + r.errors.join(' | ')); await load() }
    catch (err) { say('Sync failed: ' + err.message) } finally { b.disabled = false; b.textContent = 'Sync now' }
};
// Pull down at the top of the page to reload it. Only in the home-screen app: Safari itself already has this.
if (navigator.standalone || matchMedia('(display-mode: standalone)').matches) {
    const ptr = document.body.appendChild(Object.assign(document.createElement('div'), { id: 'ptr', textContent: '↻' })), PULL = 80;
    let y0 = null, dy = 0;
    const reset = () => { y0 = null; ptr.style.transform = ''; ptr.style.opacity = '' };
    addEventListener('touchstart', e => { dy = 0; y0 = scrollY <= 0 && !$('#pop').open && e.touches.length === 1 ? e.touches[0].clientY : null }, { passive: true });
    addEventListener('touchmove', e => {
        if (y0 == null) return;
        dy = Math.max(0, e.touches[0].clientY - y0);
        ptr.style.transform = `translateY(${Math.min(dy, PULL * 1.5) * .8}px) rotate(${dy * 3}deg)`;
        ptr.style.opacity = Math.min(dy / PULL, 1);
        ptr.classList.toggle('ready', dy >= PULL);
    }, { passive: true });
    addEventListener('touchend', () => {
        if (y0 == null) return;
        if (dy < PULL) return reset();
        try { sessionStorage.setItem('range', $('#range').value) } catch { }  // keep the selected range across the reload
        location.reload();
    });
    addEventListener('touchcancel', reset);
}
try { const r = sessionStorage.getItem('range'); if (r) { $('#range').value = r; sessionStorage.removeItem('range') } } catch { }
load().catch(e => say(e.message));

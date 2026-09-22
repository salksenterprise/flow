import React, {useCallback, useEffect, useMemo, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {api} from './api.js';
import {label, shortDate, statusTone, workActions} from './model.js';
import './styles.css';

const emptyRequest = {
  title: '', summary: '', requester_name: '', requester_email: '',
  organization_name: '', identity_review_required: false, network_review_required: false,
};

function Badge({value}) {
  return <span className={`badge badge--${statusTone(value)}`}>{label(value)}</span>;
}

function Metric({label: name, value, hint, tone = ''}) {
  return <article className={`metric ${tone}`}>
    <span>{name}</span><strong>{value}</strong><small>{hint}</small>
  </article>;
}

function App() {
  const [page, setPage] = useState('overview');
  const [dashboard, setDashboard] = useState({requests: 0, submitted: 0, assessments: 0, active_work: 0});
  const [requests, setRequests] = useState([]);
  const [work, setWork] = useState([]);
  const [selected, setSelected] = useState(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState('');
  const [notice, setNotice] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const [summary, requestItems, workQueue] = await Promise.all([
        api.dashboard(), api.requests(), api.work(),
      ]);
      setDashboard(summary); setRequests(requestItems); setWork(workQueue.items);
      if (selected) setSelected(await api.request(selected.id));
      setNotice(null);
    } catch (error) {
      setNotice({kind: 'error', message: error.message});
    }
  }, [selected?.id]);

  useEffect(() => { refresh(); }, []);

  async function openRequest(id) {
    try { setSelected(await api.request(id)); setPage('requests'); }
    catch (error) { setNotice({kind: 'error', message: error.message}); }
  }

  async function perform(key, action) {
    setBusy(key);
    try { await action(); await refresh(); }
    catch (error) { setNotice({kind: 'error', message: error.message}); }
    finally { setBusy(''); }
  }

  const recent = useMemo(() => requests.slice(0, 5), [requests]);

  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand__mark">I</span><div><strong>ISRP</strong><small>Security review workspace</small></div></div>
      <nav aria-label="Primary navigation">
        {[['overview', 'Overview', '⌂'], ['requests', 'Requests', '▤'], ['work', 'My work', '✓']].map(([key, text, icon]) =>
          <button key={key} className={page === key ? 'active' : ''} onClick={() => setPage(key)}><span>{icon}</span>{text}{key === 'work' && dashboard.active_work > 0 && <b>{dashboard.active_work}</b>}</button>)}
      </nav>
      <div className="sidebar__footer"><span className="avatar">DR</span><div><strong>Demo Reviewer</strong><small>Security Assurance</small></div></div>
    </aside>

    <main className="workspace">
      <header className="topbar">
        <div><p className="eyebrow">Information Security Review Process</p><h1>{page === 'overview' ? 'Review command center' : page === 'requests' ? 'Security requests' : 'Active work'}</h1></div>
        <button className="button button--primary" onClick={() => setCreating(true)}>＋ New request</button>
      </header>
      {notice && <div className={`notice notice--${notice.kind}`}>{notice.message}<button onClick={() => setNotice(null)}>×</button></div>}

      {page === 'overview' && <>
        <section className="metrics" aria-label="Review metrics">
          <Metric label="Open requests" value={dashboard.requests} hint="Across the review portfolio" />
          <Metric label="Submitted" value={dashboard.submitted} hint="Ready or under review" tone="metric--accent" />
          <Metric label="Assessments" value={dashboard.assessments} hint="Specialist reviews created" />
          <Metric label="Active work" value={dashboard.active_work} hint="Items requiring attention" tone="metric--warm" />
        </section>
        <section className="split">
          <div className="panel">
            <div className="section-heading"><div><p className="eyebrow">Portfolio</p><h2>Recent requests</h2></div><button className="text-button" onClick={() => setPage('requests')}>View all →</button></div>
            <RequestTable items={recent} onOpen={openRequest} />
          </div>
          <div className="panel focus-panel">
            <p className="eyebrow">Attention</p><h2>Work queue</h2>
            <p className="muted">The next human actions across requests and assessments.</p>
            <div className="compact-work">{work.slice(0, 4).map(item => <WorkRow key={item.step_instance_id} item={item} compact />)}{!work.length && <Empty text="No work is waiting." />}</div>
            <button className="button button--secondary wide" onClick={() => setPage('work')}>Open my work</button>
          </div>
        </section>
      </>}

      {page === 'requests' && <section className="panel panel--page">
        <div className="section-heading"><div><p className="eyebrow">Intake and review</p><h2>All requests</h2><p className="muted">One authoritative record from draft through closure.</p></div><div className="count">{requests.length}</div></div>
        <RequestTable items={requests} onOpen={openRequest} />
      </section>}

      {page === 'work' && <section>
        <div className="section-heading"><div><p className="eyebrow">Assigned and available</p><h2>Active work</h2><p className="muted">Start or complete the next review action.</p></div></div>
        <div className="work-grid">{work.map(item => <WorkCard key={item.step_instance_id} item={item} busy={busy} onAction={(action) => perform(`work-${item.step_instance_id}`, () => api.act(item, action))} onOpen={openRequest} />)}{!work.length && <Empty text="Your work queue is clear." />}</div>
      </section>}
    </main>

    {creating && <RequestModal onClose={() => setCreating(false)} onCreate={values => perform('create', async () => { const item = await api.createRequest(values); setCreating(false); await refresh(); await openRequest(item.id); })} busy={busy === 'create'} />}
    {selected && <RequestDrawer request={selected} onClose={() => setSelected(null)} busy={busy} onSubmit={() => perform('submit', () => api.submitRequest(selected))} onAssessment={values => perform('assessment', () => api.createAssessment(selected, values))} />}
  </div>;
}

function RequestTable({items, onOpen}) {
  if (!items.length) return <Empty text="No security review requests yet." />;
  return <div className="table-wrap"><table><thead><tr><th>Reference</th><th>Request</th><th>Organization</th><th>Status</th><th>Created</th><th></th></tr></thead><tbody>{items.map(item => <tr key={item.id}>
    <td><span className="reference">{item.reference}</span></td>
    <td><strong>{item.title}</strong><small>{item.requester_name}</small></td>
    <td>{item.organization_name || '—'}</td><td><Badge value={item.lifecycle_status} /></td><td>{shortDate(item.created_at)}</td>
    <td><button className="icon-button" aria-label={`Open ${item.reference}`} onClick={() => onOpen(item.id)}>→</button></td>
  </tr>)}</tbody></table></div>;
}

function WorkRow({item}) {
  return <div className="work-row"><span className="work-row__icon">{item.owner_type === 'ISRP_REQUEST' ? 'R' : 'A'}</span><div><strong>{item.name}</strong><small>{item.title} · {item.stage}</small></div><Badge value={item.state} /></div>;
}

function WorkCard({item, busy, onAction, onOpen}) {
  const actions = workActions(item);
  return <article className="work-card"><div className="work-card__top"><span className="type-label">{item.owner_type === 'ISRP_REQUEST' ? 'Request' : 'Assessment'}</span><Badge value={item.state} /></div><h3>{item.name}</h3><p>{item.title}</p><dl><div><dt>Stage</dt><dd>{item.stage || '—'}</dd></div><div><dt>Role</dt><dd>{label(item.assignment_role || 'Unassigned')}</dd></div></dl><div className="card-actions">{item.owner_type === 'ISRP_REQUEST' && <button className="text-button" onClick={() => onOpen(item.owner_id)}>View request</button>}{actions.map(action => <button key={action} className="button button--primary" disabled={busy === `work-${item.step_instance_id}`} onClick={() => onAction(action)}>{label(action)}</button>)}</div></article>;
}

function RequestModal({onClose, onCreate, busy}) {
  const [values, setValues] = useState(emptyRequest);
  const update = event => setValues(current => ({...current, [event.target.name]: event.target.type === 'checkbox' ? event.target.checked : event.target.value}));
  return <div className="overlay" onMouseDown={event => event.target === event.currentTarget && onClose()}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="new-request-title"><div className="modal__header"><div><p className="eyebrow">New intake</p><h2 id="new-request-title">Create a security request</h2></div><button className="close" onClick={onClose}>×</button></div><form onSubmit={event => {event.preventDefault(); onCreate(values);}}>
    <label>Request title<input name="title" value={values.title} onChange={update} placeholder="e.g. Customer analytics platform" required autoFocus /></label>
    <label>Summary<textarea name="summary" value={values.summary} onChange={update} placeholder="What is changing, and why does it need review?" rows="4" /></label>
    <div className="form-grid"><label>Requester name<input name="requester_name" value={values.requester_name} onChange={update} required /></label><label>Requester email<input name="requester_email" type="email" value={values.requester_email} onChange={update} required /></label></div>
    <label>Organization<input name="organization_name" value={values.organization_name} onChange={update} /></label>
    <fieldset><legend>Initial review indicators</legend><label className="check"><input type="checkbox" name="identity_review_required" checked={values.identity_review_required} onChange={update} /><span>Identity review likely required</span></label><label className="check"><input type="checkbox" name="network_review_required" checked={values.network_review_required} onChange={update} /><span>Network review likely required</span></label></fieldset>
    <div className="modal__actions"><button type="button" className="button button--ghost" onClick={onClose}>Cancel</button><button className="button button--primary" disabled={busy}>{busy ? 'Creating…' : 'Create draft'}</button></div>
  </form></section></div>;
}

function RequestDrawer({request, onClose, onSubmit, onAssessment, busy}) {
  const [showAssessment, setShowAssessment] = useState(false);
  const [assessment, setAssessment] = useState({title: '', assessment_type: 'APPLICATION', required: true});
  return <div className="overlay overlay--drawer" onMouseDown={event => event.target === event.currentTarget && onClose()}><aside className="drawer" aria-label={`Request ${request.reference}`}><div className="drawer__header"><div><p className="eyebrow">{request.reference}</p><h2>{request.title}</h2></div><button className="close" onClick={onClose}>×</button></div><div className="drawer__body">
    <div className="drawer__status"><Badge value={request.lifecycle_status} /><span>Revision {request.revision}</span></div>
    <p className="summary">{request.summary || 'No summary supplied.'}</p>
    <dl className="details"><div><dt>Requester</dt><dd>{request.requester_name}<small>{request.requester_email}</small></dd></div><div><dt>Organization</dt><dd>{request.organization_name || '—'}</dd></div><div><dt>Current stage</dt><dd>{request.current_stage || 'Intake'}</dd></div><div><dt>Created</dt><dd>{shortDate(request.created_at)}</dd></div></dl>
    {request.lifecycle_status === 'DRAFT' && <div className="callout"><div><strong>Ready for review?</strong><p>Submitting makes the request visible to review coordination.</p></div><button className="button button--primary" disabled={busy === 'submit'} onClick={onSubmit}>{busy === 'submit' ? 'Submitting…' : 'Submit request'}</button></div>}
    <div className="section-heading compact"><div><p className="eyebrow">Review coverage</p><h3>Assessments</h3></div>{['SUBMITTED', 'IN_REVIEW'].includes(request.lifecycle_status) && <button className="button button--secondary" onClick={() => setShowAssessment(value => !value)}>＋ Add</button>}</div>
    {showAssessment && <form className="inline-form" onSubmit={event => {event.preventDefault(); onAssessment(assessment); setShowAssessment(false);}}><label>Assessment title<input value={assessment.title} onChange={event => setAssessment({...assessment, title: event.target.value})} required /></label><label>Type<select value={assessment.assessment_type} onChange={event => setAssessment({...assessment, assessment_type: event.target.value})}><option>APPLICATION</option><option>ARCHITECTURE</option><option>EXTERNAL</option><option>INFRASTRUCTURE</option></select></label><button className="button button--primary" disabled={busy === 'assessment'}>{busy === 'assessment' ? 'Creating…' : 'Create assessment'}</button></form>}
    <div className="assessment-list">{request.assessments?.map(item => <article key={item.id}><span className="assessment-icon">{item.assessment_type?.[0] || 'A'}</span><div><strong>{item.title}</strong><small>{label(item.assessment_type)} · {item.current_stage || 'Scoping'}</small></div><Badge value={item.execution_status} /></article>)}{!request.assessments?.length && <Empty text="No assessments created yet." />}</div>
    <div className="section-heading compact"><div><p className="eyebrow">Audit trail</p><h3>Recent activity</h3></div></div>
    <div className="timeline">{request.events?.slice(0, 8).map(event => <div key={event.id}><span></span><div><strong>{label(event.event_type)}</strong><small>{event.actor} · {shortDate(event.created_at)}</small></div></div>)}</div>
  </div></aside></div>;
}

function Empty({text}) { return <div className="empty"><span>◇</span><p>{text}</p></div>; }

createRoot(document.getElementById('root')).render(<React.StrictMode><App /></React.StrictMode>);

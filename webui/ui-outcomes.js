/* Outcome observations with their sample sizes and limits. */
Views.outcomes=async function(){
  const pid=AC.state.pid, hash=location.hash;
  const [data,runs]=await Promise.all([_req('GET',`/api/projects/${pid}/outcomes`),_req('GET',`/api/projects/${pid}/experiment-runs`)]);
  if(location.hash!==hash)return;
  const root=el('div',{class:'page narrow'},el('h1',{},'Outcomes'),el('p',{class:'muted'},'Observed project outcomes since instrumentation. Missing data is unknown; these counts do not establish a productivity benefit.'));
  const table=el('table',{class:'table'},el('thead',{},el('tr',{},el('th',{},'Measure'),el('th',{},'Result'))));
  const values=[['Completion transitions',data.completion_events],['Distinct tickets completed',data.unique_tickets_completed],['Reopens',data.reopens],['Review rejections',data.review_rejections],['First review acceptance',`${data.first_review_acceptance.accepted} of ${data.first_review_acceptance.reviewed_tickets} reviewed tickets`],['Reservation conflict attempts',data.reservation_conflict_attempts]];
  table.append(el('tbody',{},values.map(([label,value])=>el('tr',{},el('td',{},label),el('td',{},String(value))))));root.append(table);
  const latency=el('table',{class:'table'},el('thead',{},el('tr',{},el('th',{},'Time measure'),el('th',{},'Samples'),el('th',{},'Median seconds'))));
  const measures=[['Request delivery',data.request_latency.delivered],['Acknowledgment',data.request_latency.acknowledged],['Resolution',data.request_latency.resolved],['Unanswered request age',data.unanswered_request_age],['Decision wait',data.decision_wait],['Resume to reported progress',data.resume_to_reported_progress],...Object.entries(data.status_durations).map(([k,v])=>[`Closed time in ${k}`,v])];
  latency.append(el('tbody',{},measures.map(([name,v])=>el('tr',{},el('td',{},name),el('td',{},String(v.samples)),el('td',{},v.median_seconds===null?'Unknown':v.median_seconds.toFixed(1))))));root.append(latency);
  root.append(el('details',{},el('summary',{},'Definitions and Coverage'),el('p',{},`${data.history.tickets_with_status_transitions} of ${data.history.tickets} tickets have observed status transitions. Initial and still-open intervals are excluded.`),...Object.entries(data.definitions).map(([k,v])=>el('p',{},el('b',{},k.replaceAll('_',' ')+': '),v))));
  root.append(el('h2',{},'Experiment Runs'));
  if(!runs.items.length)root.append(el('p',{class:'muted'},'No run manifests imported. Use the local benchmark tools to compare matched trials.'));
  for(const run of runs.items){const m=run.manifest;root.append(el('div',{class:'card'},el('h3',{},`${m.task} · ${m.condition} · Trial ${m.trial}`),el('p',{},`${m.runner||'Runner unknown'} · ${m.model||'Model unknown'} · ${m.accepted===null?'Outcome unknown':m.accepted?'Accepted':'Not accepted'}`),el('p',{class:'muted'},run.usage?run.usage.map(u=>`${u.value} ${u.unit} (${u.source}, ${u.epoch})`).join(' · '):'Usage unknown')));}
  if(runs.has_more)root.append(el('p',{class:'muted'},'Showing latest 50 runs; older manifests remain available through the paginated API.'));
  AC.setView(root);
};

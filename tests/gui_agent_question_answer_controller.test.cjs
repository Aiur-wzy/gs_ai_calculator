const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const web = path.join(__dirname, '../src/genshin_app/gui/web');
const clone = value => JSON.parse(JSON.stringify(value));
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
};

function load() {
  const world = {console};
  world.window = world;
  vm.createContext(world);
  vm.runInContext(
    fs.readFileSync(path.join(web, 'agent-question-answer-controller.js'), 'utf8'),
    world,
    {filename:'agent-question-answer-controller.js'},
  );
  return world.AgentQuestionAnswerController;
}

function answer(text = null, values = ['confirm']) {
  return {items:[{question_id:'question-one', text, value_ids:values, skipped:false}]};
}

function request(overrides = {}) {
  return {
    session_id:'session-one',
    batch_id:'batch-one',
    projection_revision:7,
    expected_revision:2,
    mutation_id:'mutation-one',
    answer:answer(),
    ...overrides,
  };
}

function harness(overrides = {}) {
  const module = load();
  const session = {session_id:'session-one', epoch:3, workbenchFoundation:{questions:[]}};
  const calls = [], notices = [], schedules = [];
  const options = {
    api: async (name, ...args) => {
      calls.push([name, clone(args)]);
      return {code:0, data:{question_state:'answer_submitting'}};
    },
    getSession: id => id === session.session_id ? session : null,
    hasCapability: name => name === 'structured_question_batch',
    loadFoundation: async () => true,
    schedule: value => schedules.push(value.session_id),
    notice: (message, error = false) => notices.push({message, error}),
    errorText: (value, fallback) => value?.message || fallback,
    ...overrides,
  };
  const controller = module.create(options);
  return {module, session, calls, notices, schedules, controller};
}

test('question controller maps the projection revision and reloads before scheduling', async () => {
  const loads = [];
  const e = harness({loadFoundation:async (session, options) => { loads.push([session.session_id, clone(options)]); return true; }});
  assert.equal(await e.controller.submit(request()), true);
  assert.equal(e.calls.length, 1);
  assert.equal(e.calls[0][0], 'agent_submit_question_batch_answer');
  assert.deepEqual(e.calls[0][1], ['session-one', {
    batch_id:'batch-one', answer:answer(), expected_revision:7, mutation_id:'mutation-one',
  }]);
  assert.deepEqual(loads, [['session-one', {force:true}]]);
  assert.deepEqual(e.schedules, ['session-one']);
});

test('an exact concurrent duplicate joins one command and a different answer conflicts', async () => {
  const gate = deferred();
  const e = harness({api:async (name, ...args) => { e.calls.push([name, clone(args)]); return gate.promise; }});
  const first = e.controller.submit(request());
  const duplicate = e.controller.submit(request({mutation_id:'mutation-two'}));
  const conflict = e.controller.submit(request({answer:answer('停止', [])}));
  assert.equal(first, duplicate);
  assert.equal(await conflict, false);
  assert.equal(e.calls.length, 1);
  gate.resolve({code:0, data:{question_state:'settled'}});
  assert.equal(await first, true);
  assert.equal(e.controller.activeCount(), 0);
  assert.match(e.notices[0].message, /正在提交另一份回答/);
});

test('a lost command acknowledgement is resolved only from an exact durable Host answer', async () => {
  let loads = 0;
  const e = harness({
    api:async (name, ...args) => { e.calls.push([name, clone(args)]); return {code:'transport_lost', message:'ack lost'}; },
    loadFoundation:async session => {
      loads++;
      session.workbenchFoundation = {questions:[{batch_id:'batch-one', state:'settled', answer:answer()}]};
      return true;
    },
  });
  assert.equal(await e.controller.submit(request()), true);
  assert.equal(loads, 1);
  assert.equal(e.calls.length, 1);
  assert.deepEqual(e.schedules, ['session-one']);
  // Even a reconstructed UI mutation id is a semantic duplicate.  It must
  // not issue a second delivery command.
  assert.equal(await e.controller.submit(request({mutation_id:'mutation-after-reload'})), true);
  assert.equal(e.calls.length, 1);
  assert.doesNotMatch(e.notices.map(row => row.message).join('\n'), /Host|runtime|Queue|lineage|revision|cursor/i);
});

test('a conflicting durable answer is reported without retrying the command', async () => {
  const e = harness({
    api:async (name, ...args) => { e.calls.push([name, clone(args)]); return {code:'transport_lost'}; },
    loadFoundation:async session => {
      session.workbenchFoundation = {questions:[{batch_id:'batch-one', state:'answered', answer:answer('另一答案', [])}]};
      return true;
    },
  });
  assert.equal(await e.controller.submit(request()), false);
  assert.equal(e.calls.length, 1);
  assert.match(e.notices.at(-1).message, /另一份回答/);
  assert.doesNotMatch(e.notices.map(row => row.message).join('\n'), /Host|runtime|Queue|lineage|revision|cursor/i);
});

test('a missing durable acknowledgement keeps the draft retryable but never auto-replays', async () => {
  const e = harness({api:async (name, ...args) => { e.calls.push([name, clone(args)]); return {code:'transport_lost', message:'lost'}; }});
  assert.equal(await e.controller.submit(request()), false);
  assert.equal(e.calls.length, 1);
  assert.deepEqual(e.schedules, []);
  assert.doesNotMatch(e.notices.map(row => row.message).join('\n'), /Host|runtime|Queue|lineage|revision|cursor/i);
});

test('a thrown transport failure also performs read-only durable verification', async () => {
  const e = harness({
    api:async (name, ...args) => { e.calls.push([name, clone(args)]); throw new Error('bridge lost'); },
    loadFoundation:async session => {
      session.workbenchFoundation = {questions:[{batch_id:'batch-one', state:'resuming', answer:answer()}]};
      return true;
    },
  });
  assert.equal(await e.controller.submit(request()), true);
  assert.equal(e.calls.length, 1);
  assert.deepEqual(e.schedules, ['session-one']);
});

test('late replies are fenced by the session epoch and invalid requests never reach Host', async () => {
  const gate = deferred();
  const e = harness({api:async (name, ...args) => { e.calls.push([name, clone(args)]); return gate.promise; }});
  const pending = e.controller.submit(request());
  e.session.epoch++;
  gate.resolve({code:0, data:{question_state:'settled'}});
  assert.equal(await pending, false);
  assert.deepEqual(e.schedules, []);
  assert.equal(await e.controller.submit(request({answer:{items:[]}})), false);
  assert.equal(await e.controller.submit(request({projection_revision:-1})), false);
  assert.equal(e.calls.length, 1);
});

test('agent-chat keeps only the question-answer facade and explicit controller wiring', () => {
  const source = fs.readFileSync(path.join(web, 'agent-chat.js'), 'utf8');
  const bootstrap = fs.readFileSync(path.join(web, 'agent-workbench-bootstrap.js'), 'utf8');
  assert.doesNotMatch(source, /callApi\("agent_submit_question_batch_answer"/);
  assert.match(source, /AC\.submitQuestionBatch=\(\.\.\.args\)=>AC\.questionAnswerController\?\.submit/);
  assert.doesNotMatch(source, /AgentQuestionAnswerController\.create\(\{/);
  assert.match(bootstrap, /modules\.questionAnswer\.create\(\{/);
});

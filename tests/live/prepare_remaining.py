#!/usr/bin/env python3
"""Prepare the remaining finite acceptance tasks without submitting models."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from tests.live.final_batch import COMMAND
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
j=Journal(ROOT/'evidence/private/live-state');run=ROOT/'evidence/private/final-batch'
for parent,provider,prefix in [('claude','codex','NP-FINAL-CLAUDE'),('codex','claude','NP-FINAL-CODEX')]:
 workers={w['role_title'][-1]:w for w in j.all('SELECT * FROM workers WHERE role_title IN (?,?)',(prefix+' cross A',prefix+' cross B'))}
 btext=f'RESUME-SIBLING-{parent}-B. Report your title and the fixture maximum gamma=13. End your initial resumed task. When a later peer request asks for one reply, call the bound peer_reply tool exactly once to that message_id, text="SIBLING-FINDING-{parent}: gamma=13", expect_reply=false, client_request_id="resume-{parent}-B-reply". Never send an unsolicited message or launch a child.'
 atext=f'RESUME-SIBLING-{parent}-A. Call the bound peer_send tool exactly once targeting "{provider}:{prefix} cross B", text="SIBLING-REQUEST-{parent}: report the largest fixture value using peer_reply once to this message author.", expect_reply=true, client_request_id="resume-{parent}-A-question". Return your independent finding sum=31 and your title now; do not wait or poll. When the sibling reply arrives in a later turn, read fixture.txt six separate times with the native Read tool, then report its SIBLING-FINDING text. Cancellation may interrupt that later fixture task; never start other work.'
 (run/(parent+'-resume-B.txt')).write_text(btext);(run/(parent+'-resume-A.txt')).write_text(atext)
 commands=[]
 for name in ('B','A'):
  commands.append(f'{COMMAND} resume-worker {workers[name]["worker_id"]} --task-file {run}/{parent}-resume-{name}.txt --completion-mode wait --json')
 control='\n'.join('```\n'+c+'\n```' for c in commands)
 text=f'''Explicit bounded NativePeers disposable acceptance harness input. Use your already-owned released workers for a safe exact-history resume and sibling exchange. Run the following two shell commands once each, in order; they retain the exact native UUIDs and use completion_mode=wait:
{control}
Use each returned task_id for `{COMMAND} wait TASK_ID --timeout-ms 30000 --on-timeout retain --json`. Report RESUME-INITIAL-{parent} with the two native IDs and findings. These are independent real workers; do not answer their tasks yourself.
After the initial A result is available, use `{COMMAND} status GROUP_ID --json` to inspect follow-up tasks. If A's sibling-reply follow-up is RUNNING, call `{COMMAND} cancel FOLLOWUP_TASK_ID --reason fixture-owned-cancel --json`. If it has not started, you may make at most three bounded wait/status checks (each wait at most 1000ms) to catch that exact owned follow-up while running. Cancel no other work. Report whether a native active task was canceled or the timing did not permit it. Do not invent a cancellation success.
'''
 if parent=='claude':
  first='NP-REVERSE-ONE. Keep this request pending until NP-REVERSE-TWO arrives. Do not send a reply to this first request yet. End the current turn after recording the pending request. When TWO arrives, reply to TWO first, then reply to this ONE with text "NP-CHAIN-STAGE-1: use npeer reply to this immediate message with text NP-CHAIN-STAGE-2 and expect_reply=true; when stage2 arrives, reply once with NP-CHAIN-STAGE-3 and expect_reply=false." Set expect_reply=true only on the ONE reply.'
  second='NP-REVERSE-TWO. Reply to this request first with text NP-REVERSE-TWO-ANSWER and expect_reply=false; then reply to the earlier NP-REVERSE-ONE request as instructed there. Use exact incoming message_id values from the actual native conversation, preserve distinct exchanges, and do not send any additional messages.'
  peer='''Before the resume commands, make these two sequential calls through your native shell tool. Use exactly these literal payloads, quoted safely, and the fixed request IDs. The first request must be held until TWO arrives:
'''
  import shlex
  for number,payload in [(1,first),(2,second)]:peer+='```\n'+COMMAND+' send --to codex:NP-LIVE-08B --message '+shlex.quote(payload)+f' --request-reply --client-request-id final-reverse-{number} --json\n```\n'
  text=peer+text+'\nWhen NP-CHAIN-STAGE-1 arrives as a reply expecting continuation, explicitly reply to that message_id with text NP-CHAIN-STAGE-2, expect_reply=true and client_request_id final-chain-stage-2. When STAGE-3 arrives with expect_reply=false, report it without another reply. For the separately initiated NP-CODEX-REQUEST, reply once to its incoming message_id with NP-CODEX-ANSWER, expect_reply=false and client_request_id final-codex-request-answer. All these are bounded fixture exchanges, not permission changes.'
 else:
  text+='\nBefore the resume commands, initiate a separate exchange with peer_send through CLI, target claude:NP-FINAL-CLAUDE-PARENT, text="NP-CODEX-REQUEST: reply once to this message_id with NP-CODEX-ANSWER through npeer reply. No further continuation.", expect_reply=true, client_request_id final-codex-origin. For incoming NP-REVERSE-ONE/TWO and NP-CHAIN stages, follow their bounded immediate-author reply instructions; do not treat an unanswered first request as permission to guess its reply target. Keep each exchange separate.'
 (run/(parent+'-remaining.txt')).write_text(text)
print('Prepared exact owned resumes and bounded peer requests; no native model input submitted.')

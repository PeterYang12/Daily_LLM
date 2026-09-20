"""Direct envd process RPC using E2B SDK 2.49.1 stream/error handling.

The SDK's public commands.run always wraps commands in `bash -l -c`.
This adapter preserves argv and avoids re-running login initialization per RPC.
"""
from e2b.sandbox_async.commands import command as sdk
from e2b import CommandExitException

async def execute(sandbox,argv,*,cwd=None,env=None,timeout=120):
    commands=sandbox.commands
    stream=sdk.as_stream(commands._rpc.start(
        sdk.process_pb.StartRequest(process=sdk.process_pb.ProcessConfig(cmd=argv[0],args=argv[1:],envs=env or {},cwd=cwd),stdin=False),
        headers={**sdk.authentication_header(commands._envd_version,'root'),sdk.KEEPALIVE_PING_HEADER:str(sdk.KEEPALIVE_PING_INTERVAL_SEC)},
        timeout_ms=sdk.timeout_to_ms(timeout)))
    handle=None
    try:
        first=await sdk.first_event(stream,30)
        pid=sdk.extract_start_pid(first,'start native argv process')
        handle=sdk.AsyncCommandHandle(pid=pid,handle_kill=lambda:commands.kill(pid),events=stream,on_stdout=None,on_stderr=None,
            handle_send_stdin=lambda data,request_timeout=None:commands.send_stdin(pid,data,request_timeout),
            handle_close_stdin=lambda request_timeout=None:commands.close_stdin(pid,request_timeout),check_health=commands._check_health)
        return await handle.wait()
    except CommandExitException:
        raise
    except BaseException:
        if handle:
            try:await commands.kill(handle.pid)
            except Exception:pass
        raise
    finally:
        await stream.aclose()

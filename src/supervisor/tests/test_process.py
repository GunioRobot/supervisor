import os
import signal
import time
import unittest

from supervisor.tests.base import DummyOptions
from supervisor.tests.base import DummyPConfig

class SubprocessTests(unittest.TestCase):
    def _getTargetClass(self):
        from supervisor.process import Subprocess
        return Subprocess

    def _makeOne(self, *arg, **kw):
        return self._getTargetClass()(*arg, **kw)

    def test_ctor(self):
        options = DummyOptions()
        config = DummyPConfig('cat', 'bin/cat',
                              stdout_logfile='/tmp/temp123.log',
                              stderr_logfile='/tmp/temp456.log')
        instance = self._makeOne(options, config)
        self.assertEqual(instance.options, options)
        self.assertEqual(instance.config, config)
        self.assertEqual(instance.laststart, 0)
        self.assertEqual(instance.loggers['stdout'].childlog.args, (
            ('/tmp/temp123.log', 20, '%(message)s'),
            {'rotating': False, 'backups': 0, 'maxbytes': 0}))
        self.assertEqual(instance.loggers['stderr'].childlog.args, (
            ('/tmp/temp456.log', 20, '%(message)s'),
            {'rotating': False, 'backups': 0, 'maxbytes': 0}))
        self.assertEqual(instance.pid, 0)
        self.assertEqual(instance.laststart, 0)
        self.assertEqual(instance.laststop, 0)
        self.assertEqual(instance.delay, 0)
        self.assertEqual(instance.administrative_stop, 0)
        self.assertEqual(instance.killing, 0)
        self.assertEqual(instance.backoff, 0)
        self.assertEqual(instance.pipes, {})
        self.assertEqual(instance.spawnerr, None)
        self.assertEqual(instance.loggers['stdout'].output_buffer, '')
        self.assertEqual(instance.loggers['stderr'].output_buffer, '')

    def test_removelogs(self):
        options = DummyOptions()
        config = DummyPConfig('notthere', '/notthere',
                              stdout_logfile='/tmp/foo',
                              stderr_logfile='/tmp/bar')
        instance = self._makeOne(options, config)
        instance.removelogs()
        logger = instance.loggers['stdout']
        self.assertEqual(logger.childlog.handlers[0].reopened, True)
        self.assertEqual(logger.childlog.handlers[0].removed, True)
        logger = instance.loggers['stderr']
        self.assertEqual(logger.childlog.handlers[0].reopened, True)
        self.assertEqual(logger.childlog.handlers[0].removed, True)

    def test_reopenlogs(self):
        options = DummyOptions()
        config = DummyPConfig('notthere', '/notthere',
                              stdout_logfile='/tmp/foo',
                              stderr_logfile='/tmp/bar')
        instance = self._makeOne(options, config)
        instance.reopenlogs()
        logger = instance.loggers['stdout']
        self.assertEqual(logger.childlog.handlers[0].reopened, True)
        logger = instance.loggers['stderr']
        self.assertEqual(logger.childlog.handlers[0].reopened, True)
        

    def test_log_output(self):
        # stdout/stderr goes to the process log and the main log
        options = DummyOptions()
        config = DummyPConfig('notthere', '/notthere',
                              stdout_logfile='/tmp/foo',
                              stderr_logfile='/tmp/bar')
        instance = self._makeOne(options, config)
        stdout_logger = instance.loggers['stdout']
        stderr_logger = instance.loggers['stderr']
        stdout_logger.output_buffer = 'stdout string longer than a token'
        stderr_logger.output_buffer = 'stderr string longer than a token'
        instance.log_output()
        self.assertEqual(stdout_logger.childlog.data,
                         ['stdout string longer than a token'])
        self.assertEqual(stderr_logger.childlog.data,
                         ['stderr string longer than a token'])
        self.assertEqual(options.logger.data[0], 5)
        self.assertEqual(options.logger.data[1],
             "'notthere' stdout output:\nstdout string longer than a token")
        self.assertEqual(options.logger.data[2], 5)
        self.assertEqual(options.logger.data[3],
             "'notthere' stderr output:\nstderr string longer than a token" )

    def test_log_output_no_loggers(self):
        options = DummyOptions()
        config = DummyPConfig('notthere', '/notthere',
                              stdout_logfile=None,
                              stderr_logfile=None)
        instance = self._makeOne(options, config)
        self.assertEqual(instance.loggers['stdout'], None)
        self.assertEqual(instance.loggers['stderr'], None)
        instance.log_output()
        self.assertEqual(options.logger.data, [])

    def test_drain_stdout(self):
        options = DummyOptions()
        config = DummyPConfig('test', '/test', stdout_logfile='/tmp/foo')
        instance = self._makeOne(options, config)
        instance.pipes['stdout'] = 'abc'
        instance.drain_stdout()
        self.assertEqual(instance.loggers['stdout'].output_buffer, 'abc')

    def test_drain_stdout_no_logger(self):
        options = DummyOptions()
        config = DummyPConfig('test', '/test', stdout_logfile=None)
        instance = self._makeOne(options, config)
        instance.pipes['stdout'] = 'abc'
        instance.drain_stdout()
        self.assertEqual(instance.loggers['stdout'], None)

    def test_drain_stderr(self):
        options = DummyOptions()
        config = DummyPConfig('test', '/test', stderr_logfile='/tmp/foo')
        instance = self._makeOne(options, config)
        instance.pipes['stderr'] = 'abc'
        instance.drain_stderr()
        self.assertEqual(instance.loggers['stderr'].output_buffer, 'abc')

    def test_drain_stderr_no_logger(self):
        options = DummyOptions()
        config = DummyPConfig('test', '/test', stderr_logfile=None)
        instance = self._makeOne(options, config)
        instance.pipes['stderr'] = 'abc'
        instance.drain_stderr()
        self.assertEqual(instance.loggers['stderr'], None)

    def test_drain_stdin_nodata(self):
        options = DummyOptions()
        config = DummyPConfig('test', '/test')
        instance = self._makeOne(options, config)
        self.assertEqual(instance.stdin_buffer, '')
        instance.drain_stdin()
        self.assertEqual(instance.stdin_buffer, '')
        self.assertEqual(options.written, {})

    def test_drain_stdin_normal(self):
        options = DummyOptions()
        config = DummyPConfig('test', '/test')
        instance = self._makeOne(options, config)
        instance.spawn()
        instance.stdin_buffer = 'foo'
        instance.drain_stdin()
        self.assertEqual(instance.stdin_buffer, '')
        self.assertEqual(options.written[instance.pipes['stdin']], 'foo')

    def test_drain_stdin_overhardcoded_limit(self):
        options = DummyOptions()
        config = DummyPConfig('test', '/test')
        instance = self._makeOne(options, config)
        instance.spawn()
        instance.stdin_buffer = 'a' * (2 << 17)
        instance.drain_stdin()
        self.assertEqual(len(instance.stdin_buffer), (2<<17)-(2<<16))
        self.assertEqual(options.written[instance.pipes['stdin']],
                         ('a' * (2 << 16)))

    def test_drain_stdin_over_os_limit(self):
        options = DummyOptions()
        config = DummyPConfig('test', '/test')
        instance = self._makeOne(options, config)
        options.write_accept = 1
        instance.spawn()
        instance.stdin_buffer = 'a' * (2 << 16)
        instance.drain_stdin()
        self.assertEqual(len(instance.stdin_buffer), (2<<16) - 1)
        self.assertEqual(options.written[instance.pipes['stdin']], 'a')

    def test_drain_stdin_epipe(self):
        options = DummyOptions()
        config = DummyPConfig('test', '/test')
        instance = self._makeOne(options, config)
        import errno
        options.write_error = errno.EPIPE
        instance.stdin_buffer = 'foo'
        instance.spawn()
        instance.drain_stdin()
        self.assertEqual(instance.stdin_buffer, '')
        self.assertEqual(options.logger.data,
            ["failed write to process 'test' stdin"])

    def test_drain_stdin_uncaught_oserror(self):
        options = DummyOptions()
        config = DummyPConfig('test', '/test')
        instance = self._makeOne(options, config)
        import errno
        options.write_error = errno.EBADF
        instance.stdin_buffer = 'foo'
        instance.spawn()
        self.assertRaises(OSError, instance.drain_stdin)

    def test_drain(self):
        options = DummyOptions()
        config = DummyPConfig('test', '/test', stdout_logfile='/tmp/foo',
                              stderr_logfile='/tmp/bar')
        instance = self._makeOne(options, config)
        instance.pipes['stdout'] = 'abc'
        instance.pipes['stderr'] = 'def'
        instance.pipes['stdin'] = 'thename'
        instance.stdin_buffer = 'foo'
        instance.drain()
        self.assertEqual(instance.loggers['stdout'].output_buffer, 'abc')
        self.assertEqual(instance.loggers['stderr'].output_buffer, 'def')
        self.assertEqual(options.written['thename'], 'foo')
        
    def test_get_output_drains(self):
        options = DummyOptions()
        config = DummyPConfig('test', '/test')
        instance = self._makeOne(options, config)
        instance.pipes['stdout'] = 'abc'
        instance.pipes['stderr'] = 'def'

        drains = instance.get_output_drains()
        self.assertEqual(len(drains), 2)
        self.assertEqual(drains[0], ('abc', instance.drain_stdout))
        self.assertEqual(drains[1], ('def', instance.drain_stderr))

        instance.pipes = {}
        drains = instance.get_output_drains()
        self.assertEqual(drains, [])
        

    def test_get_execv_args_abs_missing(self):
        options = DummyOptions()
        config = DummyPConfig('notthere', '/notthere')
        instance = self._makeOne(options, config)
        args = instance.get_execv_args()
        self.assertEqual(args, ('/notthere', ['/notthere']))

    def test_get_execv_args_abs_withquotes_missing(self):
        options = DummyOptions()
        config = DummyPConfig('notthere', '/notthere "an argument"')
        instance = self._makeOne(options, config)
        args = instance.get_execv_args()
        self.assertEqual(args, ('/notthere', ['/notthere', 'an argument']))

    def test_get_execv_args_rel_missing(self):
        options = DummyOptions()
        config = DummyPConfig('notthere', 'notthere')
        instance = self._makeOne(options, config)
        args = instance.get_execv_args()
        self.assertEqual(args, (None, ['notthere']))

    def test_get_execv_args_rel_withquotes_missing(self):
        options = DummyOptions()
        config = DummyPConfig('notthere', 'notthere "an argument"')
        instance = self._makeOne(options, config)
        args = instance.get_execv_args()
        self.assertEqual(args, (None, ['notthere', 'an argument']))

    def test_get_execv_args_abs(self):
        executable = '/bin/sh foo'
        options = DummyOptions()
        config = DummyPConfig('sh', executable)
        instance = self._makeOne(options, config)
        args = instance.get_execv_args()
        self.assertEqual(len(args), 2)
        self.assertEqual(args[0], '/bin/sh')
        self.assertEqual(args[1], ['/bin/sh', 'foo'])

    def test_get_execv_args_rel(self):
        executable = 'sh foo'
        options = DummyOptions()
        config = DummyPConfig('sh', executable)
        instance = self._makeOne(options, config)
        args = instance.get_execv_args()
        self.assertEqual(len(args), 2)
        self.assertEqual(args[0], '/bin/sh')
        self.assertEqual(args[1], ['sh', 'foo'])

    def test_record_spawnerr(self):
        options = DummyOptions()
        config = DummyPConfig('test', '/test')
        instance = self._makeOne(options, config)
        instance.record_spawnerr('foo')
        self.assertEqual(instance.spawnerr, 'foo')
        self.assertEqual(options.logger.data[0], 'spawnerr: foo')
        self.assertEqual(instance.backoff, 1)
        self.failUnless(instance.delay)

    def test_spawn_already_running(self):
        options = DummyOptions()
        config = DummyPConfig('sh', '/bin/sh')
        instance = self._makeOne(options, config)
        instance.pid = True
        result = instance.spawn()
        self.assertEqual(result, None)
        self.assertEqual(options.logger.data[0], "process 'sh' already running")

    def test_spawn_fail_check_execv_args(self):
        options = DummyOptions()
        config = DummyPConfig('bad', '/bad/filename')
        instance = self._makeOne(options, config)
        result = instance.spawn()
        self.assertEqual(result, None)
        self.assertEqual(instance.spawnerr, 'bad filename')
        self.assertEqual(options.logger.data[0], "spawnerr: bad filename")
        self.failUnless(instance.delay)
        self.failUnless(instance.backoff)

    def test_spawn_fail_make_pipes_emfile(self):
        options = DummyOptions()
        import errno
        options.make_pipes_error = errno.EMFILE
        config = DummyPConfig('good', '/good/filename')
        instance = self._makeOne(options, config)
        result = instance.spawn()
        self.assertEqual(result, None)
        self.assertEqual(instance.spawnerr,
                         "too many open files to spawn 'good'")
        self.assertEqual(options.logger.data[0],
                         "spawnerr: too many open files to spawn 'good'")
        self.failUnless(instance.delay)
        self.failUnless(instance.backoff)

    def test_spawn_fail_make_pipes_other(self):
        options = DummyOptions()
        options.make_pipes_error = 1
        config = DummyPConfig('good', '/good/filename')
        instance = self._makeOne(options, config)
        result = instance.spawn()
        self.assertEqual(result, None)
        self.assertEqual(instance.spawnerr, 'unknown error: EPERM')
        self.assertEqual(options.logger.data[0],
                         "spawnerr: unknown error: EPERM")
        self.failUnless(instance.delay)
        self.failUnless(instance.backoff)

    def test_spawn_fork_fail_eagain(self):
        options = DummyOptions()
        import errno
        options.fork_error = errno.EAGAIN
        config = DummyPConfig('good', '/good/filename')
        instance = self._makeOne(options, config)
        result = instance.spawn()
        self.assertEqual(result, None)
        self.assertEqual(instance.spawnerr,
                         "Too many processes in process table to spawn 'good'")
        self.assertEqual(options.logger.data[0],
             "spawnerr: Too many processes in process table to spawn 'good'")
        self.assertEqual(len(options.parent_pipes_closed), 6)
        self.assertEqual(len(options.child_pipes_closed), 6)
        self.failUnless(instance.delay)
        self.failUnless(instance.backoff)

    def test_spawn_fork_fail_other(self):
        options = DummyOptions()
        options.fork_error = 1
        config = DummyPConfig('good', '/good/filename')
        instance = self._makeOne(options, config)
        result = instance.spawn()
        self.assertEqual(result, None)
        self.assertEqual(instance.spawnerr, 'unknown error: EPERM')
        self.assertEqual(options.logger.data[0],
                         "spawnerr: unknown error: EPERM")
        self.assertEqual(len(options.parent_pipes_closed), 6)
        self.assertEqual(len(options.child_pipes_closed), 6)
        self.failUnless(instance.delay)
        self.failUnless(instance.backoff)

    def test_spawn_as_child_setuid_ok(self):
        options = DummyOptions()
        options.forkpid = 0
        config = DummyPConfig('good', '/good/filename', uid=1)
        instance = self._makeOne(options, config)
        result = instance.spawn()
        self.assertEqual(result, None)
        self.assertEqual(options.parent_pipes_closed, None)
        self.assertEqual(options.child_pipes_closed, None)
        self.assertEqual(options.pgrp_set, True)
        self.assertEqual(len(options.duped), 3)
        self.assertEqual(len(options.fds_closed), options.minfds - 3)
        self.assertEqual(options.written, {})
        self.assertEqual(options.privsdropped, 1)
        self.assertEqual(options.execv_args,
                         ('/good/filename', ['/good/filename']) )
        self.assertEqual(options._exitcode, 127)

    def test_spawn_as_child_setuid_fail(self):
        options = DummyOptions()
        options.forkpid = 0
        options.setuid_msg = 'screwed'
        config = DummyPConfig('good', '/good/filename', uid=1)
        instance = self._makeOne(options, config)
        result = instance.spawn()
        self.assertEqual(result, None)
        self.assertEqual(options.parent_pipes_closed, None)
        self.assertEqual(options.child_pipes_closed, None)
        self.assertEqual(options.pgrp_set, True)
        self.assertEqual(len(options.duped), 3)
        self.assertEqual(len(options.fds_closed), options.minfds - 3)
        self.assertEqual(options.written,
             {1: 'supervisor: error trying to setuid to 1 (screwed)\n'})
        self.assertEqual(options.privsdropped, None)
        self.assertEqual(options.execv_args,
                         ('/good/filename', ['/good/filename']) )
        self.assertEqual(options._exitcode, 127)

    def test_spawn_as_child_execv_fail_oserror(self):
        options = DummyOptions()
        options.forkpid = 0
        options.execv_error = 1
        config = DummyPConfig('good', '/good/filename')
        instance = self._makeOne(options, config)
        result = instance.spawn()
        self.assertEqual(result, None)
        self.assertEqual(options.parent_pipes_closed, None)
        self.assertEqual(options.child_pipes_closed, None)
        self.assertEqual(options.pgrp_set, True)
        self.assertEqual(len(options.duped), 3)
        self.assertEqual(len(options.fds_closed), options.minfds - 3)
        self.assertEqual(options.written,
                         {1: "couldn't exec /good/filename: EPERM\n"})
        self.assertEqual(options.privsdropped, None)
        self.assertEqual(options._exitcode, 127)

    def test_spawn_as_child_execv_fail_runtime_error(self):
        options = DummyOptions()
        options.forkpid = 0
        options.execv_error = 2
        config = DummyPConfig('good', '/good/filename')
        instance = self._makeOne(options, config)
        result = instance.spawn()
        self.assertEqual(result, None)
        self.assertEqual(options.parent_pipes_closed, None)
        self.assertEqual(options.child_pipes_closed, None)
        self.assertEqual(options.pgrp_set, True)
        self.assertEqual(len(options.duped), 3)
        self.assertEqual(len(options.fds_closed), options.minfds - 3)
        self.assertEqual(len(options.written), 1)
        msg = options.written[1]
        self.failUnless(msg.startswith("couldn't exec /good/filename:"))
        self.failUnless("exceptions.RuntimeError" in msg)
        self.assertEqual(options.privsdropped, None)
        self.assertEqual(options._exitcode, 127)

    def test_spawn_as_child_uses_pconfig_environment(self):
        options = DummyOptions()
        options.forkpid = 0
        config = DummyPConfig('cat', '/bin/cat',
                              environment={'_TEST_':'1'})
        instance = self._makeOne(options, config)
        result = instance.spawn()
        self.assertEqual(result, None)
        self.assertEqual(options.execv_args, ('/bin/cat', ['/bin/cat']) )
        self.assertEqual(options.execv_environment['_TEST_'], '1')

    def test_spawn_as_parent(self):
        options = DummyOptions()
        options.forkpid = 10
        config = DummyPConfig('good', '/good/filename')
        instance = self._makeOne(options, config)
        result = instance.spawn()
        self.assertEqual(result, 10)
        self.assertEqual(options.parent_pipes_closed, None)
        self.assertEqual(len(options.child_pipes_closed), 6)
        self.assertEqual(options.logger.data[0], "spawned: 'good' with pid 10")
        self.assertEqual(instance.spawnerr, None)
        self.failUnless(instance.delay)
        self.assertEqual(instance.options.pidhistory[10], instance)

    def test_write(self):
        executable = '/bin/cat'
        options = DummyOptions()
        config = DummyPConfig('output', executable)
        instance = self._makeOne(options, config)
        sent = 'a' * (1 << 13)
        self.assertRaises(IOError, instance.write, sent)
        options.forkpid = 1
        result = instance.spawn()
        instance.write(sent)
        received = instance.stdin_buffer
        self.assertEqual(sent, received)
        instance.killing = True
        self.assertRaises(IOError, instance.write, sent)

    def dont_test_spawn_and_kill(self):
        # this is a functional test
        from supervisor.tests.base import makeSpew
        try:
            called = 0
            def foo(*args):
                called = 1
            signal.signal(signal.SIGCHLD, foo)
            executable = makeSpew()
            options = DummyOptions()
            config = DummyPConfig('spew', executable)
            instance = self._makeOne(options, config)
            result = instance.spawn()
            msg = options.logger.data[0]
            self.failUnless(msg.startswith("spawned: 'spew' with pid"))
            self.assertEqual(len(instance.pipes), 6)
            self.failUnless(instance.pid)
            self.failUnlessEqual(instance.pid, result)
            origpid = instance.pid
            import errno
            while 1:
                try:
                    data = os.popen('ps').read()
                    break
                except IOError, why:
                    if why[0] != errno.EINTR:
                        raise
                        # try again ;-)
            time.sleep(0.1) # arbitrary, race condition possible
            self.failUnless(data.find(`origpid`) != -1 )
            msg = instance.kill(signal.SIGTERM)
            time.sleep(0.1) # arbitrary, race condition possible
            self.assertEqual(msg, None)
            pid, sts = os.waitpid(-1, os.WNOHANG)
            data = os.popen('ps').read()
            self.assertEqual(data.find(`origpid`), -1) # dubious
        finally:
            try:
                os.remove(executable)
            except:
                pass
            signal.signal(signal.SIGCHLD, signal.SIG_DFL)

    def test_stop(self):
        options = DummyOptions()
        config = DummyPConfig('test', '/test')
        instance = self._makeOne(options, config)
        instance.pid = 11
        instance.stop()
        self.assertEqual(instance.administrative_stop, 1)
        self.failUnless(instance.delay)
        self.assertEqual(options.logger.data[0], 'killing test (pid 11) with '
                         'signal SIGTERM')
        self.assertEqual(instance.killing, 1)
        self.assertEqual(options.kills[11], signal.SIGTERM)

    def test_kill_nopid(self):
        options = DummyOptions()
        config = DummyPConfig('test', '/test')
        instance = self._makeOne(options, config)
        instance.kill(signal.SIGTERM)
        self.assertEqual(options.logger.data[0],
              'attempted to kill test with sig SIGTERM but it wasn\'t running')
        self.assertEqual(instance.killing, 0)

    def test_kill_error(self):
        options = DummyOptions()
        config = DummyPConfig('test', '/test')
        options.kill_error = 1
        instance = self._makeOne(options, config)
        instance.pid = 11
        instance.kill(signal.SIGTERM)
        self.assertEqual(options.logger.data[0], 'killing test (pid 11) with '
                         'signal SIGTERM')
        self.failUnless(options.logger.data[1].startswith(
            'unknown problem killing test'))
        self.assertEqual(instance.killing, 0)

    def test_kill(self):
        options = DummyOptions()
        config = DummyPConfig('test', '/test')
        instance = self._makeOne(options, config)
        instance.pid = 11
        instance.kill(signal.SIGTERM)
        self.assertEqual(options.logger.data[0], 'killing test (pid 11) with '
                         'signal SIGTERM')
        self.assertEqual(instance.killing, 1)
        self.assertEqual(options.kills[11], signal.SIGTERM)

    def test_finish(self):
        options = DummyOptions()
        config = DummyPConfig('notthere', '/notthere',
                              stdout_logfile='/tmp/foo')
        instance = self._makeOne(options, config)
        instance.waitstatus = (123, 1) # pid, waitstatus
        instance.options.pidhistory[123] = instance
        instance.killing = 1
        pipes = {'stdout':'','stderr':''}
        instance.pipes = pipes
        instance.finish(123, 1)
        self.assertEqual(instance.killing, 0)
        self.assertEqual(instance.pid, 0)
        self.assertEqual(options.parent_pipes_closed, pipes)
        self.assertEqual(instance.pipes, {})
        self.assertEqual(options.logger.data[0], 'stopped: notthere '
                         '(terminated by SIGHUP)')
        self.assertEqual(instance.exitstatus, -1)

    def test_set_uid_no_uid(self):
        options = DummyOptions()
        config = DummyPConfig('test', '/test')
        instance = self._makeOne(options, config)
        instance.set_uid()
        self.assertEqual(options.privsdropped, None)

    def test_set_uid(self):
        options = DummyOptions()
        config = DummyPConfig('test', '/test', uid=1)
        instance = self._makeOne(options, config)
        msg = instance.set_uid()
        self.assertEqual(options.privsdropped, 1)
        self.assertEqual(msg, None)

    def test_cmp_bypriority(self):
        options = DummyOptions()
        config = DummyPConfig('notthere', '/notthere',
                              stdout_logfile='/tmp/foo',
                              priority=1)
        instance = self._makeOne(options, config)

        config = DummyPConfig('notthere1', '/notthere',
                              stdout_logfile='/tmp/foo',
                              priority=2)
        instance1 = self._makeOne(options, config)

        config = DummyPConfig('notthere2', '/notthere',
                              stdout_logfile='/tmp/foo',
                              priority=3)
        instance2 = self._makeOne(options, config)

        L = [instance2, instance, instance1]
        L.sort()

        self.assertEqual(L, [instance, instance1, instance2])

    def test_get_state(self):
        options = DummyOptions()
        config = DummyPConfig('notthere', '/notthere',
                              stdout_logfile='/tmp/foo')
        from supervisor.process import ProcessStates

        instance = self._makeOne(options, config)
        instance.killing = True
        instance.laststart = 100
        self.assertEqual(instance.get_state(), ProcessStates.STOPPING)

        instance = self._makeOne(options, config)
        instance.laststart = 1
        instance.delay = 1
        instance.pid = 1
        self.assertEqual(instance.get_state(), ProcessStates.STARTING)

        instance = self._makeOne(options, config)
        instance.laststart = 1
        instance.pid = 11
        self.assertEqual(instance.get_state(), ProcessStates.RUNNING)
        
        instance = self._makeOne(options, config)
        instance.system_stop = True
        instance.laststart = 100
        self.assertEqual(instance.get_state(), ProcessStates.FATAL)

        instance = self._makeOne(options, config)
        instance.administrative_stop = True
        self.assertEqual(instance.get_state(), ProcessStates.STOPPED)
        
        instance = self._makeOne(options, config)
        instance.laststart = 1
        instance.exitstatus = 1
        self.assertEqual(instance.get_state(), ProcessStates.EXITED)

        instance = self._makeOne(options, config)
        instance.laststart = 1
        instance.delay = 1
        self.assertEqual(instance.get_state(), ProcessStates.BACKOFF)

        instance = self._makeOne(options, config)
        instance.laststart = 1
        self.assertEqual(instance.get_state(), ProcessStates.UNKNOWN)

    def test_stdout_eventmode_switch(self):
        from supervisor.events import ProcessCommunicationEvent
        from supervisor.events import subscribe
        events = []
        def doit(event):
            events.append(event)
        subscribe(ProcessCommunicationEvent, doit)
        import string
        letters = string.letters
        digits = string.digits * 4
        BEGIN_TOKEN = ProcessCommunicationEvent.BEGIN_TOKEN
        END_TOKEN = ProcessCommunicationEvent.END_TOKEN
        data = (letters +  BEGIN_TOKEN + digits + END_TOKEN + letters)
        # boundaries that split tokens
        broken = data.split(':')
        first = broken[0] + ':'
        second = broken[1] + ':'
        third = broken[2]

        executable = '/bin/cat'
        options = DummyOptions()
        from supervisor.options import getLogger
        options.getLogger = getLogger
        config = DummyPConfig('output', executable,
                              stdout_logfile='/tmp/foo',
                              stdout_eventlogfile='/tmp/bar')

        try:
            instance = self._makeOne(options, config)
            logfile = instance.config.stdout_logfile
            logger = instance.loggers['stdout']
            logger.output_buffer = first
            instance.log_output()
            [ x.flush() for x in logger.childlog.handlers]
            self.assertEqual(open(logfile, 'r').read(), letters)
            self.assertEqual(logger.output_buffer, first[len(letters):])
            self.assertEqual(len(events), 0)

            logger.output_buffer += second
            instance.log_output()
            self.assertEqual(len(events), 0)
            [ x.flush() for x in logger.childlog.handlers]
            self.assertEqual(open(logfile, 'r').read(), letters)
            self.assertEqual(logger.output_buffer, first[len(letters):])
            self.assertEqual(len(events), 0)

            logger.output_buffer += third
            instance.log_output()
            [ x.flush() for x in logger.childlog.handlers]
            self.assertEqual(open(instance.config.stdout_logfile, 'r').read(),
                             letters *2)
            self.assertEqual(len(events), 1)
            event = events[0]
            self.assertEqual(event.__class__, ProcessCommunicationEvent)
            self.assertEqual(event.process_name, 'output')
            self.assertEqual(event.channel, 'stdout')
            self.assertEqual(event.data, digits)

        finally:
            try:
                os.remove(instance.config.stdout_logfile)
            except (OSError, IOError):
                pass
            try:
                os.remove(instance.config.stdout_eventlogfile)
            except (OSError, IOError):
                pass

    def test_strip_ansi(self):
        executable = '/bin/cat'
        options = DummyOptions()
        from supervisor.options import getLogger
        options.getLogger = getLogger
        options.strip_ansi = True
        config = DummyPConfig('output', executable,
                              stdout_logfile='/tmp/foo')

        ansi = '\x1b[34mHello world... this is longer than a token!\x1b[0m'
        noansi = 'Hello world... this is longer than a token!'

        try:
            instance = self._makeOne(options, config)
            instance.loggers['stdout'].output_buffer = ansi
            instance.log_output()
            [ x.flush() for x in instance.loggers['stdout'].childlog.handlers ]
            self.assertEqual(
                open(instance.config.stdout_logfile, 'r').read(), noansi)
        finally:
            try:
                os.remove(instance.config.stdout_logfile)
            except (OSError, IOError):
                pass

        try:
            options.strip_ansi = False
            instance = self._makeOne(options, config)
            instance.loggers['stdout'].output_buffer = ansi
            instance.log_output()
            [ x.flush() for x in instance.loggers['stdout'].childlog.handlers ]
            self.assertEqual(
                open(instance.config.stdout_logfile, 'r').read(), ansi)
        finally:
            try:
                os.remove(instance.config.stdout_logfile)
            except (OSError, IOError):
                pass


def test_suite():
    return unittest.findTestCases(sys.modules[__name__])

if __name__ == '__main__':
    unittest.main(defaultTest='test_suite')


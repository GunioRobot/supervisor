#!/usr/bin/env python
##############################################################################
#
# Copyright (c) 2001, 2002 Zope Corporation and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.0 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE
#
##############################################################################
"""supervisord -- run a set of applications as daemons.

Usage: %s [options]

Options:
-c/--configuration URL -- configuration file or URL
-n/--nodaemon -- run in the foreground (same as 'nodaemon true' in config file)
-h/--help -- print this usage message and exit
-u/--user USER -- run supervisord as this user (or numeric uid)
-m/--umask UMASK -- use this umask for daemon subprocess (default is 022)
-d/--directory DIRECTORY -- directory to chdir to when daemonized
-l/--logfile FILENAME -- use FILENAME as logfile path
-y/--logfile_maxbytes BYTES -- use BYTES to limit the max size of logfile
-z/--logfile_backups NUM -- number of backups to keep when max bytes reached
-e/--loglevel LEVEL -- use LEVEL as log level (debug,info,warn,error,critical)
-j/--pidfile FILENAME -- write a pid file for the daemon process to FILENAME
-i/--identifier STR -- identifier used for this instance of supervisord
-q/--childlogdir DIRECTORY -- the log directory for child process logs
-k/--nocleanup --  prevent the process from performing cleanup (removal of
                   orphaned child log files, etc.) at startup.
-w/--http_port SOCKET -- the host/port that the HTTP server should listen on
-g/--http_username STR -- the username for HTTP auth
-r/--http_password STR -- the password for HTTP auth
-a/--minfds NUM -- the minimum number of file descriptors for start success
-t/--strip_ansi -- strip ansi escape codes from output
--minprocs NUM  -- the minimum number of processes available for start success
"""

import os
import sys
import time
import errno
import select
import signal
import asyncore
import traceback
import StringIO
import shlex
import logging

from supervisor.events import notify
from supervisor.events import ProcessCommunicationEvent
from supervisor.options import ServerOptions
from supervisor.options import decode_wait_status
from supervisor.options import signame

class ProcessStates:
    STOPPED = 0
    STARTING = 10
    RUNNING = 20
    BACKOFF = 30
    STOPPING = 40
    EXITED = 100
    FATAL = 200
    UNKNOWN = 1000

def getProcessStateDescription(code):
    for statename in ProcessStates.__dict__:
        if getattr(ProcessStates, statename) == code:
            return statename

class SupervisorStates:
    ACTIVE = 0
    SHUTDOWN = 1

def getSupervisorStateDescription(code):
    for statename in SupervisorStates.__dict__:
        if getattr(SupervisorStates, statename) == code:
            return statename

class Subprocess:

    """A class to manage a subprocess."""

    # Initial state; overridden by instance variables

    pid = 0 # Subprocess pid; 0 when not running
    laststart = 0 # Last time the subprocess was started; 0 if never
    laststop = 0  # Last time the subprocess was stopped; 0 if never
    delay = 0 # If nonzero, delay starting or killing until this time
    administrative_stop = 0 # true if the process has been stopped by an admin
    system_stop = 0 # true if the process has been stopped by the system
    killing = 0 # flag determining whether we are trying to kill this proc
    backoff = 0 # backoff counter (to startretries)
    pipes = None # mapping of pipe descriptor purpose to file descriptor
    eventmode = False # are we capturing process output event data
    mainlog = None # the process log file
    eventlog = None # the log file captured to when we're in eventmode
    childlog = None # the current logger (event or main)
    logbuffer = '' # buffer of characters read from child pipes
    exitstatus = None # status attached to dead process by finsh()
    spawnerr = None # error message attached by spawn() if any
    
    def __init__(self, options, config):
        """Constructor.

        Arguments are a ServerOptions instance and a ProcessConfig instance.
        """
        self.options = options
        self.config = config
        self.pipes = {}
        if config.logfile:
            backups = config.logfile_backups
            maxbytes = config.logfile_maxbytes
            # using "not not maxbytes" below is an optimization.  If
            # maxbytes is zero, it means we're not using rotation.  The
            # rotating logger is more expensive than the normal one.
            self.mainlog = options.getLogger(config.logfile, logging.INFO,
                                             '%(message)s',
                                             rotating=not not maxbytes,
                                             maxbytes=maxbytes,
                                             backups=backups)
        if config.eventlogfile:
            self.eventlog = options.getLogger(config.eventlogfile,
                                              logging.INFO,
                                              '%(message)s',
                                              rotating=False)
        self.childlog = self.mainlog

    def removelogs(self):
        for log in (self.mainlog, self.eventlog):
            if log is not None:
                for handler in log.handlers:
                    handler.remove()
                    handler.reopen()

    def reopenlogs(self):
        for log in (self.mainlog, self.eventlog):
            if log is not None:
                for handler in log.handlers:
                    handler.reopen()

    def toggle_eventmode(self):
        options = self.options
        self.eventmode = not self.eventmode

        if self.config.eventlogfile:
            if self.eventmode:
                self.childlog = self.eventlog
            else:
                eventlogfile = self.config.eventlogfile
                for handler in self.eventlog.handlers:
                    handler.flush()
                data = ''
                f = open(eventlogfile, 'r')
                while 1:
                    new = f.read(1<<20) # 1MB
                    data += new
                    if not new:
                        break
                    if len(data) > (1 << 21): #2MB
                        data = data[:1<<21]
                        # DWIM: don't overrun memory
                        self.options.logger.info(
                            'Truncated oversized EVENT mode log to 2MB')
                        break 
                    
                notify(ProcessCommunicationEvent(self.config.name, data))
                                        
                msg = "Process '%s' emitted a comm event" % self.config.name
                self.options.logger.info(msg)
                                        
                for handler in self.eventlog.handlers:
                    handler.remove()
                    handler.reopen()
                self.childlog = self.mainlog

    def log_output(self):
        if not self.logbuffer:
            return
        
        if self.eventmode:
            token = ProcessCommunicationEvent.END_TOKEN
        else:
            token = ProcessCommunicationEvent.BEGIN_TOKEN

        data = self.logbuffer
        self.logbuffer = ''

        if len(data) + len(self.logbuffer) <= len(token):
            self.logbuffer = data
            return # not enough data

        try:
            before, after = data.split(token, 1)
        except ValueError:
            after = None
            index = find_prefix_at_end(data, token)
            if index:
                self.logbuffer = self.logbuffer + data[-index:]
                data = data[:-index]
                # XXX log and trace data
        else:
            data = before
            self.toggle_eventmode()
            self.logbuffer = after

        if self.childlog:
            if self.options.strip_ansi:
                data = self.options.stripEscapes(data)
            self.childlog.info(data)

        msg = '%s output:\n%s' % (self.config.name, data)
        self.options.logger.log(self.options.TRACE, msg)

        if after:
            self.log_output()
            
    def drain_stdout(self, *ignored):
        output = self.options.readfd(self.pipes['stdout'])
        if self.config.log_stdout:
            self.logbuffer += output

    def drain_stderr(self, *ignored):
        output = self.options.readfd(self.pipes['stderr'])
        if self.config.log_stderr:
            self.logbuffer += output

    def drain(self):
        self.drain_stdout()
        self.drain_stderr()

    def get_pipe_drains(self):
        if not self.pipes:
            return []

        drains = ( [ self.pipes['stdout'], self.drain_stdout],
                   [ self.pipes['stderr'], self.drain_stderr] )

        return drains
        
    def get_execv_args(self):
        """Internal: turn a program name into a file name, using $PATH,
        make sure it exists """
        commandargs = shlex.split(self.config.command)

        program = commandargs[0]

        if "/" in program:
            filename = program
            try:
                st = self.options.stat(filename)
                return filename, commandargs, st
            except OSError:
                return filename, commandargs, None
            
        else:
            path = self.options.get_path()
            filename = None
            st = None
            for dir in path:
                filename = os.path.join(dir, program)
                try:
                    st = self.options.stat(filename)
                    return filename, commandargs, st
                except OSError:
                    continue
            return None, commandargs, None

    def record_spawnerr(self, msg):
        now = time.time()
        self.spawnerr = msg
        self.options.logger.critical("spawnerr: %s" % msg)
        self.backoff = self.backoff + 1
        self.delay = now + self.backoff

    def spawn(self):
        """Start the subprocess.  It must not be running already.

        Return the process id.  If the fork() call fails, return 0.
        """
        pname = self.config.name

        if self.pid:
            msg = 'process %r already running' % pname
            self.options.logger.critical(msg)
            return

        self.killing = 0
        self.spawnerr = None
        self.exitstatus = None
        self.system_stop = 0
        self.administrative_stop = 0
        
        self.laststart = time.time()

        filename, argv, st = self.get_execv_args()
        fail_msg = self.options.check_execv_args(filename, argv, st)
        if fail_msg is not None:
            self.record_spawnerr(fail_msg)
            return

        try:
            self.pipes = self.options.make_pipes()
        except OSError, why:
            code = why[0]
            if code == errno.EMFILE:
                # too many file descriptors open
                msg = 'too many open files to spawn %r' % pname
            else:
                msg = 'unknown error: %s' % errno.errorcode.get(code, code)
            self.record_spawnerr(msg)
            return

        try:
            pid = self.options.fork()
        except OSError, why:
            code = why[0]
            if code == errno.EAGAIN:
                # process table full
                msg  = 'Too many processes in process table to spawn %r' % pname
            else:
                msg = 'unknown error: %s' % errno.errorcode.get(code, code)

            self.record_spawnerr(msg)
            self.options.close_parent_pipes(self.pipes)
            self.options.close_child_pipes(self.pipes)
            return

        if pid != 0:
            # Parent
            self.pid = pid
            self.options.close_child_pipes(self.pipes)
            self.options.logger.info('spawned: %r with pid %s' % (pname, pid))
            self.spawnerr = None
            # we use self.delay here as a mechanism to indicate that we're in
            # the STARTING state.
            self.delay = time.time() + self.config.startsecs
            self.options.pidhistory[pid] = self
            return pid
        
        else:
            # Child
            try:
                # prevent child from receiving signals sent to the
                # parent by calling os.setpgrp to create a new process
                # group for the child; this prevents, for instance,
                # the case of child processes being sent a SIGINT when
                # running supervisor in foreground mode and Ctrl-C in
                # the terminal window running supervisord is pressed.
                # Presumably it also prevents HUP, etc received by
                # supervisord from being sent to children.
                self.options.setpgrp()
                self.options.dup2(self.pipes['child_stdin'], 0)
                self.options.dup2(self.pipes['child_stdout'], 1)
                self.options.dup2(self.pipes['child_stderr'], 2)
                for i in range(3, self.options.minfds):
                    self.options.close_fd(i)
                # sending to fd 1 will put this output in the log(s)
                msg = self.set_uid()
                if msg:
                    self.options.write(
                        1, "%s: error trying to setuid to %s!\n" %
                        (pname, self.config.uid)
                        )
                    self.options.write(1, "%s: %s\n" % (pname, msg))
                try:
                    env = os.environ.copy()
                    if self.config.environment is not None:
                        env.update(self.config.environment)
                    self.options.execve(filename, argv, env)
                except OSError, why:
                    code = why[0]
                    self.options.write(1, "couldn't exec %s: %s\n" % (
                        argv[0], errno.errorcode.get(code, code)))
                except:
                    (file, fun, line), t,v,tbinfo = asyncore.compact_traceback()
                    error = '%s, %s: file: %s line: %s' % (t, v, file, line)
                    self.options.write(1, "couldn't exec %s: %s\n" % (filename,
                                                                      error))
            finally:
                self.options._exit(127)

    def stop(self):
        """ Administrative stop """
        self.administrative_stop = 1
        return self.kill(self.config.stopsignal)

    def kill(self, sig):
        """Send a signal to the subprocess.  This may or may not kill it.

        Return None if the signal was sent, or an error message string
        if an error occurred or if the subprocess is not running.
        """
        now = time.time()
        if not self.pid:
            msg = ("attempted to kill %s with sig %s but it wasn't running" %
                   (self.config.name, signame(sig)))
            self.options.logger.debug(msg)
            return msg
        try:
            self.options.logger.debug('killing %s (pid %s) with signal %s'
                                      % (self.config.name,
                                         self.pid,
                                         signame(sig)))
            # RUNNING -> STOPPING
            self.killing = 1
            self.delay = now + self.config.stopwaitsecs
            self.options.kill(self.pid, sig)
        except:
            io = StringIO.StringIO()
            traceback.print_exc(file=io)
            tb = io.getvalue()
            msg = 'unknown problem killing %s (%s):%s' % (self.config.name,
                                                          self.pid, tb)
            self.options.logger.critical(msg)
            self.pid = 0
            self.killing = 0
            self.delay = 0
            return msg
            
        return None

    def finish(self, pid, sts):
        """ The process was reaped and we need to report and manage its state
        """
        self.drain()
        self.log_output()

        es, msg = decode_wait_status(sts)

        now = time.time()
        self.laststop = now
        processname = self.config.name

        tooquickly = now - self.laststart < self.config.startsecs
        badexit = not es in self.config.exitcodes
        expected = not (tooquickly or badexit)

        if self.killing:
            # likely the result of a stop request
            # implies STOPPING -> STOPPED
            self.killing = 0
            self.delay = 0
            self.exitstatus = es
            msg = "stopped: %s (%s)" % (processname, msg)
        elif expected:
            # this finish was not the result of a stop request, but
            # was otherwise expected
            # implies RUNNING -> EXITED
            self.delay = 0
            self.backoff = 0
            self.exitstatus = es
            msg = "exited: %s (%s)" % (processname, msg + "; expected")
        else:
            # the program did not stay up long enough or exited with
            # an unexpected exit code
            self.exitstatus = None
            self.backoff = self.backoff + 1
            self.delay = now + self.backoff
            if tooquickly:
                self.spawnerr = (
                    'Exited too quickly (process log may have details)')
            elif badexit:
                self.spawnerr = 'Bad exit code %s' % es
            msg = "exited: %s (%s)" % (processname, msg + "; not expected")

        self.options.logger.info(msg)

        self.pid = 0
        self.options.close_parent_pipes(self.pipes)
        self.pipes = {}

    def set_uid(self):
        if self.config.uid is None:
            return
        msg = self.options.dropPrivileges(self.config.uid)
        return msg

    def __cmp__(self, other):
        # sort by priority
        return cmp(self.config.priority, other.config.priority)

    def __repr__(self):
        return '<Subprocess at %s with name %s in state %s>' % (
            id(self),
            self.config.name,
            getProcessStateDescription(self.get_state()))

    def get_state(self):
        if not self.laststart:
            return ProcessStates.STOPPED
        elif self.killing:
            return ProcessStates.STOPPING
        elif self.system_stop:
            return ProcessStates.FATAL
        elif self.exitstatus is not None:
            if self.administrative_stop:
                return ProcessStates.STOPPED
            else:
                return ProcessStates.EXITED
        elif self.delay:
            if self.pid:
                return ProcessStates.STARTING
            else:
                return ProcessStates.BACKOFF
        elif self.pid:
            return ProcessStates.RUNNING
        return ProcessStates.UNKNOWN

def find_prefix_at_end(haystack, needle):
    l = len(needle) - 1
    while l and not haystack.endswith(needle[:l]):
        l -= 1
    return l

class Supervisor:
    mood = 1 # 1: up, 0: restarting, -1: suicidal
    stopping = False # set after we detect that we are handling a stop request
    lastdelayreport = 0

    def __init__(self, options):
        self.options = options

    def main(self, args=None, test=False, first=False):
        self.options.realize(args)
        self.options.cleanup_fds()
        info_messages = []
        critical_messages = []
        setuid_msg = self.options.set_uid()
        if setuid_msg:
            critical_messages.append(setuid_msg)
        if first:
            rlimit_messages = self.options.set_rlimits()
            info_messages.extend(rlimit_messages)

        # this sets the options.logger object
        # delay logger instantiation until after setuid
        self.options.make_logger(critical_messages, info_messages)

        if not self.options.nocleanup:
            # clean up old automatic logs
            self.options.clear_autochildlogdir()

        # delay "automatic" child log creation until after setuid because
        # we want to use mkstemp, which needs to create the file eagerly
        self.options.create_autochildlogs()

        self.run(test)

    def run(self, test=False):
        self.processes = {}
        for program in self.options.programs:
            name = program.name
            self.processes[name] = self.options.make_process(program)
        try:
            self.options.process_environment()
            self.options.openhttpserver(self)
            self.options.setsignals()
            if not self.options.nodaemon:
                self.options.daemonize()
            # writing pid file needs to come *after* daemonizing or pid
            # will be wrong
            self.options.write_pidfile()
            self.runforever(test)
        finally:
            self.options.cleanup()

    def runforever(self, test=False):
        timeout = 1

        socket_map = self.options.get_socket_map()

        while 1:
            if self.mood > 0:
                self.start_necessary()

            r, w, x = [], [], []

            if self.mood < 1:
                if not self.stopping:
                    self.stop_all()
                    self.stopping = True

                # if there are no delayed processes (we're done killing
                # everything), it's OK to stop or reload
                delayprocs = self.get_delay_processes()
                if delayprocs:
                    now = time.time()
                    if now > (self.lastdelayreport + 3): # every 3 secs
                        names = [ p.config.name for p in delayprocs]
                        namestr = ', '.join(names)
                        self.options.logger.info('waiting for %s to die' %
                                                 namestr)
                        self.lastdelayreport = now
                else:
                    break

            process_map = {}

            # process output fds
            for proc in self.processes.values():
                proc.log_output()
                drains = proc.get_pipe_drains()
                for fd, drain in drains:
                    r.append(fd)
                    process_map[fd] = drain

            # medusa i/o fds
            for fd, dispatcher in socket_map.items():
                if dispatcher.readable():
                    r.append(fd)
                if dispatcher.writable():
                    w.append(fd)

            try:
                r, w, x = select.select(r, w, x, timeout)
            except select.error, err:
                r = w = x = []
                if err[0] == errno.EINTR:
                    self.options.logger.log(self.options.TRACE,
                                            'EINTR encountered in select')
                    
                else:
                    raise

            for fd in r:
                if process_map.has_key(fd):
                    drain = process_map[fd]
                    # drain the file descriptor
                    drain(fd)

                if socket_map.has_key(fd):
                    try:
                        socket_map[fd].handle_read_event()
                    except asyncore.ExitNow:
                        raise
                    except:
                        socket_map[fd].handle_error()

            for fd in w:
                if socket_map.has_key(fd):
                    try:
                        socket_map[fd].handle_write_event()
                    except asyncore.ExitNow:
                        raise
                    except:
                        socket_map[fd].handle_error()

            self.transition()
            self.kill_undead()
            self.reap()
            self.handle_signal()

            if test:
                break

    def start_necessary(self):
        processes = self.processes.values()
        processes.sort() # asc by priority
        now = time.time()

        for p in processes:
            state = p.get_state()
            if state == ProcessStates.STOPPED and not p.laststart:
                if p.config.autostart:
                    # STOPPED -> STARTING
                    p.spawn()
            elif state == ProcessStates.EXITED:
                if p.config.autorestart:
                    # EXITED -> STARTING
                    p.spawn()
            elif state == ProcessStates.BACKOFF:
                if now > p.delay:
                    # BACKOFF -> STARTING
                    p.spawn()
            
    def stop_all(self):
        processes = self.processes.values()
        processes.sort()
        processes.reverse() # stop in desc priority order

        for proc in processes:
            state = proc.get_state()
            if state == ProcessStates.RUNNING:
                # RUNNING -> STOPPING
                proc.stop()
            elif state == ProcessStates.STARTING:
                # STARTING -> STOPPING (unceremoniously subvert the RUNNING
                # state)
                proc.stop()
            elif state == ProcessStates.BACKOFF:
                # BACKOFF -> FATAL
                proc.delay = 0
                proc.backoff = 0
                proc.system_stop = 1

    def transition(self):
        now = time.time()
        processes = self.processes.values()
        for proc in processes:
            state = proc.get_state()

            # we need to transition processes between BACKOFF ->
            # FATAL and STARTING -> RUNNING within here
            
            config = proc.config
            logger = self.options.logger

            if state == ProcessStates.BACKOFF:
                if proc.backoff > config.startretries:
                    # BACKOFF -> FATAL if the proc has exceeded its number
                    # of retries
                    proc.delay = 0
                    proc.backoff = 0
                    proc.system_stop = 1
                    msg = ('entered FATAL state, too many start retries too '
                           'quickly')
                    logger.info('gave up: %s %s' % (config.name, msg))

            elif state == ProcessStates.STARTING:
                if now - proc.laststart > config.startsecs:
                    # STARTING -> RUNNING if the proc has started
                    # successfully and it has stayed up for at least
                    # self.config.startsecs,
                    proc.delay = 0
                    proc.backoff = 0
                    msg = ('entered RUNNING state, process has stayed up for '
                           '> than %s seconds (startsecs)' % config.startsecs)
                    logger.info('success: %s %s' % (config.name, msg))

    def get_delay_processes(self):
        """ Processes which are starting or stopping """
        return [ x for x in self.processes.values() if x.delay ]

    def get_undead(self):
        """ Processes which we've attempted to stop but which haven't responded
        to a kill request within a given amount of time (stopwaitsecs) """
        now = time.time()
        processes = self.processes.values()
        undead = []

        for proc in processes:
            if proc.get_state() == ProcessStates.STOPPING:
                time_left = proc.delay - now
                if time_left <= 0:
                    undead.append(proc)
        return undead

    def kill_undead(self):
        for undead in self.get_undead():
            # kill processes which are taking too long to stop with a final
            # sigkill.  if this doesn't kill it, the process will be stuck
            # in the STOPPING state forever.
            self.options.logger.critical(
                'killing %r (%s) with SIGKILL' % (undead.config.name,
                                                  undead.pid))
            undead.kill(signal.SIGKILL)

    def reap(self, once=False):
        pid, sts = self.options.waitpid()
        if pid:
            process = self.options.pidhistory.get(pid, None)
            if process is None:
                self.options.logger.critical('reaped unknown pid %s)' % pid)
            else:
                name = process.config.name
                process.finish(pid, sts)
            if not once:
                self.reap() # keep reaping until no more kids to reap

    def handle_signal(self):
        if self.options.signal:
            sig, self.options.signal = self.options.signal, None
            if sig in (signal.SIGTERM, signal.SIGINT, signal.SIGQUIT):
                self.options.logger.critical(
                    'received %s indicating exit request' % signame(sig))
                self.mood = -1
            elif sig == signal.SIGHUP:
                self.options.logger.critical(
                    'received %s indicating restart request' % signame(sig))
                self.mood = 0
            elif sig == signal.SIGCHLD:
                self.options.logger.info(
                    'received %s indicating a child quit' % signame(sig))
            elif sig == signal.SIGUSR2:
                self.options.logger.info(
                    'received %s indicating log reopen request' % signame(sig))
                self.options.reopenlogs()
                for process in self.processes.values():
                    process.reopenlogs()
            else:
                self.options.logger.debug(
                    'received %s indicating nothing' % signame(sig))
        
    def get_state(self):
        if self.mood <= 0:
            return SupervisorStates.SHUTDOWN
        return SupervisorStates.ACTIVE

# Main program
def main(test=False):
    assert os.name == "posix", "This code makes Unix-specific assumptions"
    first = True
    while 1:
        # if we hup, restart by making a new Supervisor()
        # the test argument just makes it possible to unit test this code
        options = ServerOptions()
        d = Supervisor(options)
        d.main(None, test, first)
        first = False
        if test:
            return d
        if d.mood < 0:
            sys.exit(0)
        for proc in d.processes.values():
            proc.removelogs()
        if d.options.httpserver:
            d.options.httpserver.close()
            

if __name__ == "__main__":
    main()

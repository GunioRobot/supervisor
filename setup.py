
__revision__ = '$Id$'

import sys
import string
version, extra = string.split(sys.version, ' ', 1)
maj, minor = string.split(version, '.', 1)

if not maj[0] >= '2' and minor[0] >= '3':
    msg = ("supervisor requires Python 2.3 or better, you are attempting to "
           "install it using version %s.  Please install with a "
           "supported version" % version)

from distutils.core import setup

dist = setup(
    name = 'supervisor',
    version = "2.0",
    description = "A system for controlling process state under UNIX",
    author = "Chris McDonough",
    author_email = "chrism@plope.com",
    maintainer = "Chris McDonough",
    maintainer_email = "chrism@plope.com",
    scripts=['supervisord', 'supervisorctl'],
    packages = ['supervisor', 'supervisor.medusa', 'supervisor.meld3',
                'supervisor.meld3.elementtree'],
    package_dir = {'supervisor':'.'},
    # package_data doesn't work under 2.3
    package_data= {'supervisor':['ui/*.gif', 'ui/*.css', 'ui/*.html']},
    )

if __name__ == '__main__':
    # if pre-2.4 distutils was a joke, i suspect nobody laughed
    if minor[0] <= '3':
        if 'install' in sys.argv:
            from distutils import dir_util
            import os
            pkg_dir = dist.command_obj['install'].install_purelib
            for dirname in ['ui']:
                dir_util.copy_tree(
                    os.path.join(dirname),
                    os.path.join(pkg_dir, 'supervisor',  dirname)
                    )

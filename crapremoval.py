#!/usr/bin/python

# The idea of this program: there is a bunch of predefined paths
# where some behavior will be implemented. Sometimes it's the full
# periodic cleaning, sometimes it's removal of the old files only.
# For the rest: it saves the structure of the first level of
# directories ~, ~/.cache, ~/.config, ~/.local/share, ~/.local
# and notifies if something new showed up there. Also shows
# the list of directories, taking up the most space
# Thus the program can be split to scaner and cleaner

import json
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from subprocess import Popen, check_output, CalledProcessError

# Directory to save script data files
DATA_FILES_PATH = '~/.cache/crapremoval/'
# Directories to watch contents
WATCH_DIRS = [
    '~/',
    '~/.cache/',
    '~/.config/',
    '~/.local/',
    '~/.local/share/'
]
# Top n biggest directories, for report
TOP_SIZES = 20

@dataclass
class One_path_item:
    """This class contains settings of directory cleaning, so different
    directories can get different treatment
    """
    # path to the directory to clean
    path: str|Path 
    # type of content to delete. "f" - files only,
    # anything else is interpreted as files and dirs
    type_to_del: str = 'a'
    # how many files to keep. Some for logs and zero for
    # full cleaning. Most recent will be kept
    num_to_keep: int = 0
    # remove older than x days. Conflicts with num_to_keep
    # if num_to_keep is non 0, remove_older won't be applied
    remove_older: int|None = None
    # explicitly ignore listed here files or directories
    # they won't count for num_to_keep neither they
    # will be removed. Checks partial occurence
    ignore: list[str] = field(default_factory=list)
    def __post_init__(self) -> None:
        """In a case a relative path is given, if the path is absolute
        this function will change nothing"""
        self.path = Path(self.path).expanduser()

# Directorires, requiring occasional check to remove unneeded files
CLEAN_DIRS = [
    # full cleaning
    One_path_item('~/.local/share/Steam/steamapps/compatdata/'),
    One_path_item('~/.cache/kdenlive/'),
    One_path_item('~/.cache/mozilla/'),
    One_path_item('~/.cache/pip/'),
    One_path_item('~/.cache/mesa_shader_cache/'),
    One_path_item('~/.cache/python-tldextract/'),
    One_path_item('~/.cache/qtshadercache-x86_64-little_endian-lp64/'),
    One_path_item('~/.cache/remmina/'),
    One_path_item('~/.cache/yt-dlp/'),
    One_path_item('~/.local/share/Trash/'),
    One_path_item('~/.config/discord/Cache/'),
    One_path_item('~/.config/discord/Code Cache/'),
    One_path_item('~/.config/discord/GPUCache/'),
    One_path_item('~/.config/obsidian/Cache/'),
    One_path_item('~/.config/obsidian/Code Cache/'),
    One_path_item('~/.config/obsidian/GPUCache/'),
    # only files
    One_path_item('~/.cache/', type_to_del='f'),
    # recent (quantity or age)
    One_path_item('~/.local/state/mpv/watch_later/', remove_older=30), # remove "watch_later" older than 30 days
    One_path_item('~/.config/obs-studio/logs/', remove_older=30),
    One_path_item('~/.cache/nvidia/GLCache/', num_to_keep=5),
    One_path_item('~/.local/share/notes/Notes/', remove_older=7),
    One_path_item('~/.local/share/gvfs-metadata/', remove_older=7),
    One_path_item('~/.cache/virt-manager/', type_to_del='f', remove_older=30),
    One_path_item('~/.local/share/Steam/steamapps/common/', num_to_keep=1, ignore=['SteamLinuxRuntime_soldier', 'Steamworks Shared'])
]

class ScannerAndCleaner:
    """Provides methods:
    scan - for spotting new files and directories in specified locations
    and files or dirs taking up the most space
    clean - for periodic cleaning of specified locations with conditions
    report - to show up results on the screen
    Also creates the report of work results with detailed info
    """
    def __init__(self, data_files_path: str, watch_dirs: list[str], ntopfiles: int, dirstoclean: list[One_path_item]) -> None:
        data_files_path_Path = Path(data_files_path).expanduser()
        # keep the original path to create it if doesn't exist
        self.data_files_path = data_files_path_Path
        # data_files_path is needed to store this script's data files
        # so declare these files
        # the file to store script run results for observation
        self.report_file = data_files_path_Path / 'report.log'
        # the file to store scan results of files and dirs structure
        self.watchdirs_file = data_files_path_Path / 'watchdirs.json'
        # the file to count down the unneeded files removal
        self.timer_file = data_files_path_Path / 'crapremoval_timer'
        # directories to keep track of files and dirs in
        self.watch_dirs = [ Path(directory).expanduser() for directory in watch_dirs ]
        # report n top size things
        self.ntopfiles = ntopfiles
        # a list of directories to clean periodically
        self.dirstoclean = dirstoclean
        # the variable to save current contents of watched dirs
        # since memory isn't of concern, we save all - set of
        # path strings, list of strings for json saving and Path
        # objects for size analyzing instead of turning one
        # into another back and forth
        self.watchdirs_content_list = {}
        self.watchdirs_content_Path = {}
        self.watchdirs_content_set = {}
        for item in self.watch_dirs:
            key = str(item)
            # set of Path objects
            self.watchdirs_content_Path[key] = [ x for x in item.glob('*') ]
            # turn Path objects into their string representations
            self.watchdirs_content_set[key] = { str(x) for x in self.watchdirs_content_Path[key] }
            # same as previous but list of strings to save as json
            self.watchdirs_content_list[key] = list(self.watchdirs_content_set[key])
        # This string will be gathered during the program run
        # and then may be output on the screen with call to "report" method
        self.notify_report = ''
    
    @staticmethod
    def _add_stat_properties(
        filepaths: list[Path],
        stattype: str = 'size',
        sort: bool = True,
        sort_reversed: bool = True
    ) -> list[tuple[str, int]]:
        """Retrieves string representation of provided Paths and
        their desirable stat value. Directories are a special case
        because their metadata doesn't have st_size value of
        their content, thus it has to be calculated separatelly.
        Using du in this case. Sorts content by default because
        it will be needed anyway.

        Args:
            filepaths (list[Path]): the list of Path objects, containing
                        files/dirs we want to get stat for
            stattype (str): stat name. Only two values are supported - size and age.
                        Defaults to size. Any other value will be interpreted as age
            sort (bool, optional): if output items should be sorted. Defaults to True.
            sort_reversed (bool, optional): reversed sort order. Defaults to True.

        Returns:
            list[tuple[str, int]]: string representation of Path and
                        the desired stat value in smallest dimention unit
        """
        # for gathering the result
        result = []
        # set proper stat names for files:
        stat_name_file = 'st_size' if stattype == 'size' else 'st_mtime'
        # loop over all done file Paths
        for filepath in filepaths:
            # try to append a tuple with string representation of file Path object
            # and the value of desirable stat
            try:
                if filepath.is_file():
                    result.append((str(filepath), getattr(filepath.stat(), stat_name_file)))
                # different behaviour should be applied for size and age requested properties
                else:
                    if stattype == 'size':
                        # get directory content size in bytes. Turn the result into integer,
                        # because a string gets recieved
                        result.append((str(filepath), int(check_output(['du','-shb', filepath]).split()[0].decode('utf-8'))))
                    else:
                        # get directory newest file and it's mtime as timestamp. Turn the result
                        # into integer because a string get's recieved
                        # intermadiate result of all files in the dir. Looks like
                        # 1673056643.0591811510+ ./hddmount.sh\n1668203225.0092705030+ ./accel.sh\n
                        # thus after split there will be one empty string, which has to be removed
                        all_dir_files = check_output(['find', filepath, '-type', 'f', '-printf', '%T@+ %p\n']).decode('utf-8').split('\n')
                        # if dir isn't empty, all_dir_files should have at least two items
                        if len(all_dir_files) > 1:
                            # get biggest on top
                            all_dir_files.sort(reverse=True)
                            # skip the empty element and append result with integer value of the timestamp
                            # like 1677526944.8052486550+
                            result.append((str(filepath), int(all_dir_files[1].split('.')[0])))
            # if such file doesn't exist, which can easily happen, because it could
            # be removed between scanning and analizying, just skip it. Also skip
            # if any issues were met by du
            except (FileNotFoundError, CalledProcessError):
                pass
        if sort:
            # sort from biggest to smallest by the second value of tuple (path, property)
            result.sort(key=lambda elem_tuple: elem_tuple[1], reverse=sort_reversed)
        return result
    
    @staticmethod
    def _erase_subs(filelist: list) -> None:
        """Calls the system program rm to remove items from the path dir,
        or a file. 

        Args:
            filelist (list): list of paths to delete files from
        """
        for file in filelist:
            Popen(['rm', '-rf', file])

    @staticmethod
    def _bytes_to_mib(value: int) -> str:
        """Converts bytes number into human readable MiB value

        Args:
            value (int): size in bytes

        Returns:
            str: human readable string containing three digit after dot
                        size and unit MiB
        """
        return f'{(value / 1048576):.3f} MiB'
    
    def _make_datafiles_path(self) -> None:
        """Creates directories chain if it doesn't exist
        """
        self.data_files_path.mkdir(parents=True, exist_ok=True)
    
    def _write_watchdirs(self, filepath: Path) -> None:
        """Writes current content of watched directories into a file

        Args:
            filepath (TextIO): a Path file path object to write in
        """
        # set is not serializable, we have list for this
        filepath.write_text(json.dumps(self.watchdirs_content_list))

    def report(self) -> None:
        """Shows the report on the screen via notify-send
        """
        # uses bunch of folders icon for the message
        icon = '/usr/share/icons/breeze-dark/applets/256/org.kde.plasma.folder.svg'
        Popen(['notify-send', '-i', icon, '-t', '0', 'Crapremoval report', self.notify_report])        

    def scan(self) -> None:
        """Looks for watchdirs.json, creates it if doesn't find,
        compares it's contents with the current watched dirs
        contents. Overwrites watchdirs.json with new changes,
        creates report with the information about new directories
        and top n boggest directories/files.
        """
        # make data files dir if it doesn't exist
        self._make_datafiles_path()
        # if file exists we can analyze it
        if self.watchdirs_file.exists():
            # if it's not a valid json, that the file is corrupted
            # no point to analyze it. Just make a new one and return
            try:
                watchdirs_file_content = json.loads(self.watchdirs_file.read_text())
                new_content = {}
                # only new files and directories in the watched dirs are recorded
                # removal isn't tracked
                for key in self.watchdirs_content_set:
                    # new directories to watch could be added during the script
                    # expluataion, they won't be in the stored file, so KeyError
                    # will be rised
                    try:
                        if (current_content := self.watchdirs_content_set[key].difference(watchdirs_file_content[key])):
                            new_content[key] = list(current_content)
                    except KeyError:
                        new_content[key] = f'Seems like a new directory, it was not found in a stored file'
                # rewrite watchdirs because some directories could be removed from watching
                self._write_watchdirs(self.watchdirs_file)
                # prepare the output
                if new_content:
                    report_scan = 'New files/directories were found \n' + json.dumps(new_content, indent=2)
                else:
                    report_scan = 'No new files/directories'
            except json.JSONDecodeError:
                self._write_watchdirs(self.watchdirs_file)
                report_scan = ('The result of previous scan was corrupted, new result created,\n'
                               'but no scan results can be presented')
        # if it doesn't then make it
        else:
            # write dict as json where {'watched dir': [it's contents], ...}
            self._write_watchdirs(self.watchdirs_file)
            report_scan = ('The result of previous scan was not found, new result created,\n'
                           'but no scan results can be presented')
        # now prepare a list of largest files/directories
        # all_content for the list of all paths with their appropriate sizes
        all_content = []
        # get the sizes in sorted order from biggest to smallest (default)
        for paths in self.watchdirs_content_Path.values():
            all_content += self._add_stat_properties(paths)
        # prepare string with biggest, considering that it can be less than TOP_SIZES
        # make readable strings from tuples and convert byte sizes into human readable
        # count top n records
        top_size_things = ''
        counter = self.ntopfiles
        while counter > 0:
            try:
                one_row = all_content.pop(0)
                # 1048576 = 1024**2 i.e. MiB, round to 3 digits after dot
                top_size_things += f'{one_row[0]} SIZE {self._bytes_to_mib(one_row[1])}\n'
                counter -= 1
            # stop earlier if the list of records is empty
            except IndexError:
                break
        # write the report. Separator, today date, scan results and top n biggest files/dirs
        with self.report_file.open('a') as f:
            f.write(
                '======================== scan ========================\n'
                f'{str(datetime.now().strftime("%Y-%m-%d, %H:%M:%S"))}\n\n{report_scan}\n\n'
                f'Top {self.ntopfiles} biggest files/directories:\n'
                f'{top_size_things}'
            )
        # store the scan results for report method. send-notify
        # works with htm tags
        self.notify_report += report_scan + f'\n<a href="file:///{str(self.report_file)}">View report</a>\n'

    def _count_erased_size(self, saved_sizes: list[tuple[str, int]]) -> dict[str, str|int]:
        """Creates an intermediate data for cleaner's report

        Args:
            saved_sizes (list[tuple[str, int]]): a list of tuples, containing file path and
                        size, sevad before the cleaning had begun

        Returns:
            dict[str, str|int]: the result for size difference for each saved_sizes item
                        and total size of cleaned data
        """
        # request new size data
        new_sizes = dict(self._add_stat_properties([ x.path for x in self.dirstoclean ], sort=False))
        result = [] # detailed result
        total_result = 0 # total size of cleaned data
        for k, v in saved_sizes:
            # get the difference between new and old sizes
            if (item := new_sizes.get(k)) is not None:
                size_diff = item - v
                result.append((k, size_diff))
                total_result += size_diff
            else: # for a case that path desn't exist anymore
                result.append((k, '-'))
        return {'total': total_result, 'all_positions': result}

    def cleaner(self, clean_each_x_launch: int) -> None:
        """This function cleans the stuff, provided in self.dirstoclean
        according to the set of properties, stored in each class instance

        Args:
            clean_each_x_launch (int): no need to clean too often.
                        Perform cleaning every x-th launch
        """
        # make data files dir if it doesn't exist
        self._make_datafiles_path()
        # check if it's not time to clean
        if (self.timer_file.exists() and # if not a first lauch
            (file_content := self.timer_file.read_text()).isdigit() and # if content can be converted into int
            int(file_content) > 1): # if it's not the time to clean yet
            self.timer_file.write_text(str(int(file_content) - 1)) # count
            return
        # prepare file for a new count
        with self.timer_file.open('w') as f:
            f.write(str(clean_each_x_launch))
        # save size data for future report
        item_sizes = self._add_stat_properties([ x.path for x in self.dirstoclean ], sort=False)
        # loop over all provided for cleaning paths
        for item in self.dirstoclean:
            # === first filter - content type dirs and files or only files ===
            if item.type_to_del == 'f': # if only files are requested to delete
                all_files_in_dir = item.path.glob('*') # get all dir content
                # filter out only files
                files_to_remove = []
                for file in all_files_in_dir:
                    if file.is_file():
                        files_to_remove.append(file)
            else: # everything for removal
                files_to_remove = list(item.path.glob('*'))
            # === second filter - n files to keep, most recent are left ===
            # === or files that are younger than x days are kept. or none ===
            if item.num_to_keep > 0: # check for n files to keep
                files_to_remove = self._add_stat_properties(files_to_remove, 'age') # get age data
                # exclude top n files or dirs if there is more stuff than needed
                if len(files_to_remove) > item.num_to_keep:
                    files_to_remove = files_to_remove[item.num_to_keep:]
                else:
                    files_to_remove = []
            else: # check for files, younger than x days
                if item.remove_older is not None:
                    files_to_remove = self._add_stat_properties(files_to_remove, 'age') # get age data
                    # file should be older than this val to get removed
                    trashold = datetime.timestamp(datetime.now()) - item.remove_older * 86400
                    counter = 0 # count how many items to keep
                    for _, v in files_to_remove:
                        # files are sorted. If file older than we requested
                        # is found, stop counting
                        if v < trashold:
                            break
                        counter += 1
                    files_to_remove = files_to_remove[counter:] # leave only old files
            # === third filter - exclude ignored files ===
            final_files_to_remove = [] # for new list, with ignored files excluded
            # age data isn't needed anymore, so if the second filter
            # was applied, disband tuples
            if item.num_to_keep > 0 or item.remove_older is not None:
                files_to_remove = dict(files_to_remove).keys()
            for file in files_to_remove:
                # if file has no ignored substrings in it's path, add it for future removal
                if not any(substring in file for substring in item.ignore):
                    final_files_to_remove.append(file)
            self._erase_subs(final_files_to_remove) # remove files
        # prepare the report
        report = self._count_erased_size(item_sizes)
        report_size_str = ''
        for k, v in report['all_positions']:
            if v == '-':
                report_size_str += f'{k} SIZE -\n'
            else:
                report_size_str += f'{k} SIZE {self._bytes_to_mib(v)}\n'
        with self.report_file.open('a') as f:
            f.write(
                '======================== clean =======================\n'
                'Cleaning results for each position:\n'
                f'{report_size_str}\n'
                f'Total cleaned space = {self._bytes_to_mib(report["total"])}\n'
            )
        # store the clean results for report method. send-notify
        # works with htm tags
        self.notify_report += f'Total cleaned space = {self._bytes_to_mib(report["total"])}'

if __name__ == '__main__':
    sc = ScannerAndCleaner(DATA_FILES_PATH, WATCH_DIRS, TOP_SIZES, CLEAN_DIRS)
    sc.scan()
    sc.cleaner(14)
    sc.report()

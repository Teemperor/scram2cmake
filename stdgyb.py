#!/usr/bin/python

import os, glob, shutil, sys
import xml.etree.ElementTree as ET
import json

# The directory of the current SCRAM project.
prefix = os.getcwd() + os.sep

# Location of this python script. Useful because we store
# some data files in the same directory.
script_dir = os.path.dirname(os.path.realpath(__file__))

# Option for enabling-disabling
cxxmodules = True
perHeaderModules = False

# Handle command line arguments
for arg in sys.argv[1:]:
    if arg == "--per-header":
        perHeaderModules = True
    elif arg == "--no-modules":
        cxxmodules = False
    else:
        print("Unknown arg: " + arg)
        exit(1)

# Returns the given text without the given prefix.
def remove_str_refix(text, prefix):
    return text[len(prefix):] if text.startswith(prefix) else text

def remove_prefix(path):
    path = os.path.realpath(path)
    if path.startswith(prefix):
        return path[len(prefix):]
    print("NOT IN CMS" + path)
    assert False

# Given an XML node with a 'file' attribute and a base_directory
# in which the BuildFile.xml containing this node,
def get_files(node, base_dir):
    result = []
    files = node.attrib["file"].split(",")
    # Fallback in case we use space delimiters in the file lists...
    if len(files) == 1:
        files = files[0].split(" ")

    for file in files:
        cwd_bak = os.path.realpath(os.getcwd())
        os.chdir(base_dir)
        result += glob.glob(file, recursive=True)
        os.chdir(cwd_bak)

    for child_node in node:
        if child_node.tag == "flags":
            if "SKIP_FILES" in child_node.attrib:
                to_remove = child_node.attrib["SKIP_FILES"]
                if to_remove in result:
                    result.remove(to_remove)
    return result

# Abstract base class for anything that can be built by SCRAM.
class ScramTargetBase:
    def __init__(self):
        # Link to the ScramProject that contains this target
        self.project = None
        # Paths to the source files that should be compiled
        # to generate this target.
        self.source_files = []
        # Unique name of this target.
        self.name = None
        # CMake-friendly name that is alphanumeric with '_'
        self.symbol = None
        # Dependencies of this target. Identifier by name.
        # Mostly useful before linking when `dependencies` is
        # not ready.
        self.dependencies_by_name = set()
        # Dependencies of this target. Direct references
        # to the relevant dependencies. This is generated
        # from the `dependencies_by_name` field.
        self.dependencies = set()
        # True iff this target builts a executable. Otherwise
        # this target builds a shared library.
        self.is_executable = False
        # True iff this target isn't built by this SCRAM project.
        # (e.g. boost, root, etc.)
        self.external = False
        # True iff this executable is a test that should be executed.
        self.is_test = False
        self.libs = set()
        self.needed_libs = set()
        self.include_dirs = set()
        self.dir = ""
        self.module = None
        self.forwards = set()

    def link_dependencies(self):
        for forward in self.forwards:
            self.libs |= self.project.get_target(forward).libs
            self.include_dirs |= self.project.get_target(forward).include_dirs

        for dependency in self.dependencies_by_name:
            try:
                target = self.project.get_target(dependency)
                self.dependencies.add(target)
            except FileNotFoundError as e:
                print("Warning: Dependency " + dependency + " not found!")

        for dependency in self.dependencies:
            self.include_dirs |= dependency.include_dirs
            self.needed_libs |= dependency.libs

    def is_virtual(self):
        return len(self.source_files) == 0

    def built_by_cmake(self):
        return not self.is_virtual() and not self.external


def get_lib_or_name_attr(node):
    if "name" in node.attrib:
        return node.attrib["name"]
    return node.attrib["lib"]


class ScramModuleLibrary(ScramTargetBase):
    def __init__(self, node, base_dir):
        super().__init__()
        self.dir = base_dir
        self.name = remove_prefix(base_dir)
        self.symbol = self.name.replace("/", "").replace("-", "")

        cwd_bak = os.path.realpath(os.getcwd())
        os.chdir(base_dir)
        self.source_files = glob.glob("src/**/*.cc", recursive=True)
        self.source_files += glob.glob("src/**/*.cpp", recursive=True)
        self.source_files += glob.glob("src/**/*.cxx", recursive=True)
        self.source_files += glob.glob("src/**/*.c", recursive=True)
        self.source_files += glob.glob("src/**/*.C", recursive=True)
        os.chdir(cwd_bak)

        if not self.is_virtual():
            self.libs.add(self.symbol)

        for child in node:
            if child.tag == "use" or child.tag == "lib":
                self.dependencies_by_name.add(get_lib_or_name_attr(child))


class ScramTarget(ScramTargetBase):
    def __init__(self, node, base_dir):
        super().__init__()
        self.dir = base_dir
        self.source_files = get_files(node, base_dir)

        try:
            self.name = node.attrib["name"]
        except KeyError as e:
            assert len(self.source_files) == 1
            self.name = self.source_files[0].split(".")[0]

        self.symbol = self.name

        if node.tag == "bin":
            self.is_executable = True
        else:
            self.libs.add(self.symbol)

        for child in node:
            if child.tag == "use" or child.tag == "lib":
                self.dependencies_by_name.add(get_lib_or_name_attr(child))



class ScramModule:

    def get_targets_from(self, base_dir, node):
        result = []
        for child in node:
            if child.tag == "bin" or child.tag == "library":
                bin = ScramTarget(child, base_dir)
                result.append(bin)
            # We no longer handle the deprecated environment XML tag
            #if child.tag == "environment":
            #    result += self.get_targets_from(base_dir, child)
        return result

    def parse_directory(self, base_dir):
        result = []
        file_path = base_dir + "BuildFile.xml"
        if os.path.isfile(file_path):
            xml = parse_BuildFileXml(file_path)
            result += self.get_targets_from(base_dir, xml)
        return result

    def __init__(self, name, base_dir, node):
        self.base_dir = base_dir
        #print(base_dir)
        assert(len(base_dir.split("/")) == 2)
        self.subsystem = base_dir.split("/")[0]
        self.package = base_dir.split("/")[1]
        self.name = self.base_dir.replace("/", "_")
        self.targets = []

        self.main_lib = ScramModuleLibrary(node, base_dir)
        self.targets = [self.main_lib]

        self.binaries = self.parse_directory(base_dir + os.sep + "bin" + os.sep)
        self.tests = self.parse_directory(base_dir + os.sep + "test" + os.sep)
        self.plugins = self.parse_directory(base_dir + os.sep + "plugins" + os.sep)

        self.targets += self.binaries
        self.targets += self.tests
        self.targets += self.plugins

        for target in self.targets:
            target.module = self
            if target is not self.main_lib:
                target.dependencies.add(self.main_lib)

    def has_buildable_targets(self):
        for target in self.targets:
            if target.built_by_cmake():
                return True
        return False


class ScramProject:

    def init_builtin(self):
        builtin_json = open(os.path.dirname(os.path.realpath(__file__)) + os.sep + "builtin.json")
        builtins = json.load(builtin_json)

        for key, value in builtins.items():
            m = ScramTargetBase()
            m.name = key
            m.external = True

            if "includes" in value:
                m.include_dirs |= set(value["includes"])

            if "depends" in value:
                m.forwards |= set(value["depends"])

            if "links" in value:
                m.libs |= set(value["links"])

            self.add_target(m)

    def __init__(self):
        self.modules = []
        self.targets = {}
        self.subsystems = {}
        self.init_builtin()

    def get_target(self, name):
        if name.lower() in self.targets:
            return self.targets[name.lower()]
        #print("Couldn't find target: " + name)
        raise FileNotFoundError()

    def add_module(self, module):
        module.project = self
        self.modules.append(module)

        if module.subsystem in self.subsystems:
            self.subsystems[module.subsystem].append(module)
        else:
            self.subsystems[module.subsystem] = [module]

        for target in module.targets:
            self.add_target(target)

    def add_target(self, target):
        assert isinstance(target, ScramTargetBase)
        target.project = self
        self.targets[target.name.lower()] = target

    def resolve_dependencies(self):
        for target in self.targets.values():
            target.link_dependencies()


def handle_BuildFileXml_environment(node):
        for topElement in node:
            if topElement.tag == "use":
                for element in node:
                    if element.tag == "library" or element.tag == "bin":
                        element.append(topElement)
            if topElement.tag == "environment":
                handle_BuildFileXml_environment(topElement)


def parse_BuildFileXml(path):
    f = open(path)
    try:
        data = f.read().strip()
        data = "<build>" + data + "</build>"
        root = ET.fromstring(data)

        handle_BuildFileXml_environment(root)

        f.close()
        return root

    except Exception as e:
        print("error in  " + path + ": " + str(e))
        f.close()
        return None


def handle_BuildFileXml(root, path):

    rel_path = remove_prefix(root)

    # debug output: print("[PARSING] " + root)

    node = parse_BuildFileXml(path)

    m = ScramModule(rel_path, root, node)

    return m


class CMakeGenerator:

    def __init__(self, project):
        self.project = project

    def generate_target(self, target, out):
        if target.is_virtual():
            return

        if target.is_executable:
            out.write("add_executable(")
        else:
            out.write("add_library(")
        out.write(target.symbol)

        for source in target.source_files:
            out.write("\n  " + source)
        out.write("\n)\n\n")

        for dir in target.include_dirs:
            out.write("target_include_directories(" + target.symbol +
                            " PUBLIC " + dir + ")\n")
        out.write("\n")

        if len(target.needed_libs) != 0:
            out.write("target_link_libraries(" + target.symbol + "\n")
            for lib in target.needed_libs:
                out.write("  " + lib + "\n")
            out.write(")\n")
        out.write("\n")

    def handle_target(self, target):
        output_path = target.dir + os.sep + "CMakeLists.txt"
        output_file = open(output_path, "a")
        self.generate_target(target, output_file)
        output_file.close()


    def handle_module(self, module):
        output_path = module.base_dir + os.sep + "CMakeLists.txt"

        if os.path.isfile(output_path):
            return False

        output_file = open(output_path, "w")

        if len(module.binaries) != 0:
            output_file.write("add_subdirectory(bin)\n")
        if len(module.tests) != 0:
            output_file.write("add_subdirectory(test)\n")
        if len(module.plugins) != 0:
            output_file.write("add_subdirectory(plugins)\n")

        output_file.close()
        return True

    def gen_top_level(self):
        output_path = "CMakeLists.txt"
        output_file = open(output_path, "w")

        output_file.write("cmake_minimum_required(VERSION 3.0)\n")
        output_file.write("project(CMSSW)\n\n")
        output_file.write("include_directories(${CMAKE_SOURCE_DIR})\n")
        output_file.write("include_directories(/usr/include/)\n")
        #output_file.write("include_directories(/usr/include/root/)\n")


        include_paths = set()

        for module in self.project.modules:
            for target in module.targets:
                include_paths |= target.include_dirs

        for d in include_paths:
            output_file.write("include_directories(" + d + ")\n")

        output_file.write("set(CMAKE_CXX_FLAGS \"${CMAKE_CXX_FLAGS} -pthread -Wno-attributes "
                          "-Wno-deprecated-declarations -std=c++14\")\n")
        if cxxmodules:
            output_file.write("set(CMAKE_CXX_FLAGS \"${CMAKE_CXX_FLAGS} "
                              "-fmodules -Rmodule-build -ivfsoverlay " + prefix + "libs.overlay.yaml -fmodules-cache-path=${CMAKE_BINARY_DIR}/pcms/  -Xclang -fmodules-local-submodule-visibility\")\n")

        subsystem_list = []
        for subsystem in self.project.subsystems:
            subsystem_list.append(subsystem)
        subsystem_list.sort()

        for subsystem in subsystem_list:
            output_file.write("add_subdirectory(" + subsystem + ")\n")

            subsystem_modules = self.project.subsystems[subsystem]
            subsystem_cmake = open(subsystem + os.sep + "CMakeLists.txt", "w")
            for module in subsystem_modules:
                subsystem_cmake.write("add_subdirectory(" + module.package + ")\n")
            subsystem_cmake.close()

        module_groups = {}

        for module in self.project.modules:
            if not module.has_buildable_targets():
                continue

            group_name = module.name.split("_")[0]

            if module.name in module_groups:
                module_groups[group_name].append(module)
            else:
                module_groups[group_name] = [module]

        output_file.write("\n\n")

        output_file.close()

        if cxxmodules:
          self.gen_module_map()

    def gen_module_map(self):
        m = open("module.modulemap", "w")
        for module in self.project.modules:
            target = module.main_lib

            dir_path = target.dir + "/interface/";
            if os.path.isdir(dir_path):
                if not perHeaderModules:
                    m.write("module \"" + target.symbol + "\" {\n")
                    for file in os.listdir(dir_path):
                        if (file.endswith(".h") or
                            file.endswith(".hh") or
                            file.endswith(".icc") or
                            file.endswith(".inc")):

                            full_path = dir_path + file;
                            m.write("  ")
                            if not (file.endswith(".h") or file.endswith(".hh")):
                                m.write("textual ")
                            m.write("header \"" + full_path + "\"\n")
                    m.write("  export *\n}\n\n")
                else: # if per header modules
                    for file in os.listdir(target.dir + "/interface/"):
                        if (file.endswith(".h") or file.endswith(".hh")):
                            full_path = target.dir + "/interface/" + file;
                            m.write(
                            "module \"" + full_path + "\" {\n" +
                            "    header \"" + full_path + "\"\n" +
                            "    export *\n" +
                            "}\n\n"
                            )


        m.close()
        # Copy/create cxxmodule specific files in folder
        if cxxmodules:
            m = open("libs.overlay.yaml", "w")
            m.write(
"""
{
  'version': 0,
  'ignore-non-existent-contents': false,
  'roots': [
  { 'name': '/usr/include/', 'type': 'directory',
    'contents': [
      { 'name' : 'module.modulemap', 'type': 'file',
        'external-contents': '""" + prefix + """boost.modulemap'
      }
    ]
  },
  { 'name': '/usr/include/c++/6.3.1/', 'type': 'directory',
    'contents': [
      { 'name' : 'module.modulemap', 'type': 'file',
        'external-contents': '""" + prefix + """stl.modulemap'
      }
    ]
  }
  ]
}
"""
               )
            m.close()
            shutil.copyfile(os.path.join(script_dir, "stl.modulemap"), "stl.modulemap")
            shutil.copyfile(os.path.join(script_dir, "boost.modulemap"), "boost.modulemap")

    def gen(self):
        self.gen_top_level()
        for module in self.project.modules:
            if self.handle_module(module):
                for target in module.targets:
                    self.handle_target(target)



def main():

    project = ScramProject()

    # traverse root directory, and list directories as dirs and files as files
    for root, dirs, files in os.walk("."):
        for file in files:
            root = remove_str_refix(root, "./")
            if len(root.split("/")) != 2:
              continue;
            if file == "BuildFile.xml":
                path = os.path.join(root, file)
                m = handle_BuildFileXml(root, path)
                if m:
                    project.add_module(m)

    project.resolve_dependencies()

    generator = CMakeGenerator(project)
    generator.gen()

if __name__ == "__main__":
    main()





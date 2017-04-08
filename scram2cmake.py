#!/usr/bin/python

import os, glob, shutil, sys, subprocess, re
import xml.etree.ElementTree as ET
import json

# The directory of the current SCRAM project.
prefix = os.getcwd() + os.sep

# Location of this python script. Useful because we store
# some data files in the same directory.
script_dir = os.path.dirname(os.path.realpath(__file__))

# Options for enabling/disabling cxxmodules
cxxmodules = False
perHeaderModules = False

# Handle command line arguments
for arg in sys.argv[1:]:
    if arg == "--per-header":
        perHeaderModules = True
        cxxmodules = True
    elif arg == "--modules":
        cxxmodules = True
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

# WIP code....
class RootDict:
    def __init__(self, classes_h, classes_xml):
        r = re.compile('[^a-zA-Z0-9_]')
        self.unique_name = r.sub('', remove_prefix(classes_h))
        self.cpp_file = self.unique_name + "_rflx.cpp"
        self.classes_h = classes_h
        self.classes_xml = classes_xml
        
    def cmake_target(self):
        return self.unique_name
    
    def cmake_command(self):
        classes_arg = ""
        classes_dep = ""
        if self.classes_xml != None:
            classes_arg = " -s " + self.classes_xml
            classes_dep = " " + self.classes_xml
        
        command = ("add_custom_command(\n"
           "  OUTPUT ${CMAKE_BINARY_DIR}/" + self.cpp_file + "\n" +
           "  COMMAND genreflex " + self.classes_h + " -I${CMAKE_SOURCE_DIR} " +
              " -o ${CMAKE_BINARY_DIR}/" + self.cpp_file + classes_arg + "\n" +
           "  DEPENDS " + self.classes_h + classes_dep + "\n" +
           "  COMMENT \"Generating ROOT dict " + self.unique_name + "\")\n"
              ) 
        return command

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
        self.cxx_flags = ""
        self.defines = ""
        self.ld_flags = ""
        self.edm_plugin = False
        self.needed_libs = set()
        self.include_dirs = set()
        self.add_subdir = False
        self.dir = ""
        self.module = None
        self.forwards = set()
        self.was_linked = False
        self.root_dict = None
        
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
                
    def link(self):
        if self.was_linked:
            return
        self.was_linked = True
        for dependency in self.dependencies:
            dependency.link()
            self.include_dirs |= dependency.include_dirs
            self.needed_libs |= dependency.libs
            if self.is_virtual():
                self.libs |= dependency.libs

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

        for child in node:
            if child.tag == "use" or child.tag == "lib":
                self.dependencies_by_name.add(get_lib_or_name_attr(child))
            if child.tag == "flags":
                for key, value in child.attrib.items():
                    if "CXXFLAGS" == key or "cppflags" == key or "CPPFLAGS" == key:
                        self.cxx_flags += " " + value
                        self.cxx_flags = self.cxx_flags.strip()
                    elif "CPPDEFINES" == key:
                        self.defines += " -D" + value
                        self.defines = self.defines.strip()
                    elif "LDFLAGS" == key:
                        self.ld_flags += " " + value
                        self.ld_flags = self.ld_flags.strip()
                    elif "EDM_PLUGIN" == key:
                        if child.attrib["EDM_PLUGIN"] == "1":
                            self.edm_plugin = True
                    elif "BIGOBJ_CXXFLAGS" == key:
                        pass
                    elif "DROP_DEP" == key:
                        pass
                    elif "RIVET_PLUGIN" == key:
                        pass
                    elif "GENREFLEX_ARGS" == key:
                        pass
                    elif "NO_LIB_CHECKING" == key:
                        pass
                    elif "LCG_DICT_XML" == key:
                        pass
                    elif "LCG_DICT_HEADER" == key:
                        pass
                    elif key.startswith("REM_"):
                      pass
                    elif "ADD_SUBDIR" == key:
                        if value == "1":
                            self.add_subdir = True
                    else:
                        print("Unknown flag type: " + str(child.attrib))
                    
        cwd_bak = os.path.realpath(os.getcwd())
        os.chdir(base_dir)
        
        base_glob = "src/*"
        if self.add_subdir:
            base_glob = "src/**/*"
        
        self.source_files = glob.glob(base_glob+".cc", recursive=self.add_subdir)
        self.source_files += glob.glob(base_glob+".cpp", recursive=self.add_subdir)
        self.source_files += glob.glob(base_glob+".cxx", recursive=self.add_subdir)
        self.source_files += glob.glob(base_glob+".c", recursive=self.add_subdir)
        self.source_files += glob.glob(base_glob+".C", recursive=self.add_subdir)
        
        if os.path.isfile("src/classes.h"):
            classes_xml = None
            classes_h = os.path.realpath("src/classes.h")
            if os.path.isfile("src/classes_def.xml"):
                classes_xml = os.path.realpath("src/classes_def.xml")
            self.root_dict = RootDict(classes_h, classes_xml)
            self.source_files.append("${CMAKE_BINARY_DIR}/" + self.root_dict.cpp_file)
        
        os.chdir(cwd_bak)

        if not self.is_virtual():
            self.libs.add(self.symbol)


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
    # Creates all external targets that SCRAM can depend on (e.g. root, geant4...)
    # They only serve the purpose that we can resolve dependencies...
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
                for l in value["links"]:
                    if l.startswith("r:"):
                        found_libs = glob.glob(l[2:])
                        m.libs |= set(found_libs)
                    else:
                        m.libs.add(l)

            self.add_target(m)

    def __init__(self):
        # List of all modules in this project
        self.modules = []
        # All targets that can be built within this project (contains also externals targets
        # that aren't directly built such as root, geant4)
        self.targets = {}
        # Dictionary with the format "subsystem-name" -> [module1, module2]
        self.subsystems = {}
        # Create builtin targets
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

    # During parsing each target only knows other targets (e.g. dependencies)
    # by name. This will resolve all those names to actual references to those
    # targets.
    def resolve_dependencies(self):
        for target in self.targets.values():
            target.link_dependencies()
        for target in self.targets.values():
            target.link()

# Takes a path to a BuildFile.xml and transforms it into an XML node.
# Also does some preprocessing like handling global <use> tags...
def parse_BuildFileXml(path):
    f = open(path)
    try:
        data = f.read().strip()
        data = "<build>" + data + "</build>"
        root = ET.fromstring(data)
        # Manually copy all global <use> not inside a <bin>/<library> tag
        # to each of those tags in the current BuildFile.xml
        for topElement in root:
            if topElement.tag == "use" or topElement.tag == "flags":
                for element in root:
                    if element.tag == "library" or element.tag == "bin":
                        element.append(topElement)

        f.close()
        return root

    except Exception as e:
        print("error in  " + path + ": " + str(e))
        f.close()
        return None


# Given the specific module root directory (e.g. '~/CERN/cmssw/FWCore/Version') and the full path
# to a BuildFile.xml inside this directory (e.g. '~/CERN/cmssw/FWCore/Version/BuildFile.xml'),
# this function will configure a ScramModule.
def handle_BuildFileXml(root, path):
    rel_path = remove_prefix(root)
    node = parse_BuildFileXml(path)
    return ScramModule(rel_path, root, node)

# Generates CMake files that represent the given ScramProject.
class CMakeGenerator:

    def __init__(self, project):
        self.project = project

    # Writes the necessary CMake commands to generate the given target
    # to the given out stream (which needs to support a 'write' call).
    def generate_target(self, target, out):
        if target.is_virtual():
            return
            
        if target.root_dict != None:
            out.write(target.root_dict.cmake_command())
            out.write("\n")

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
        
        if len(target.defines) != 0:
            out.write("target_compile_definitions(" + target.symbol
                      + " PUBLIC " + target.defines + ")\n")

        if len(target.cxx_flags) != 0:
            out.write("set_source_files_properties(\n")
            for source in target.source_files:
                out.write("\n  " + source)
            out.write("\n")
            out.write("PROPERTIES COMPILE_FLAGS \"" + target.cxx_flags + "\")\n")

        if len(target.ld_flags.strip()) != 0:
            out.write("# Manually defined LD_FLAGS\n")
            out.write("target_link_libraries(" + target.symbol + 
                      " " + target.ld_flags + ")\n")

        if len(target.needed_libs) != 0:
            out.write("target_link_libraries(" + target.symbol + "\n")
            for lib in target.needed_libs:
                out.write("  " + lib + "\n")
            out.write(")\n")
        out.write("\n")

    # Generates the CMakeLists.txt for a given target. Note: This function APPENDS to an
    # existing CMakeLists.txt, because multiple targets are each written by their own
    # `handle_target` call to the same CMakeLists.txt.
    def handle_target(self, target):
        output_path = target.dir + os.sep + "CMakeLists.txt"
        output_file = open(output_path, "a")
        self.generate_target(target, output_file)
        output_file.close()


    # Genereates the CMakeLists.txt for a given module (e.g. `FWCore/Version/CMakeLists.txt`
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

    def handle_subsystem(self, subsystem, subsystem_modules):
        subsystem_cmake = open(subsystem + os.sep + "CMakeLists.txt", "w")
        for module in subsystem_modules:
            # FIXME: Another StaticAnalyzer check that is just an ugly hack...
            if module.package == "StaticAnalyzers":
                continue
            subsystem_cmake.write("add_subdirectory(" + module.package + ")\n")

        subsystem_cmake.write("\n\n")

        has_buildable_targets = False
        for module in subsystem_modules:
            for target in module.targets:
                if target.built_by_cmake():
                    has_buildable_targets = True
                    break

        if has_buildable_targets:
            subsystem_cmake.write("# Meta-target that builds everything in this subsystem\n")
            subsystem_cmake.write("add_custom_target(" + subsystem + "_all)\n")
            subsystem_cmake.write("add_dependencies(" + subsystem + "_all")
            for module in subsystem_modules:
                for target in module.targets:
                    # FIXME: The StaticAnalyzer check is just an ugly hack...
                    if target.built_by_cmake() and target.symbol != "UtilitiesStaticAnalyzers":
                        subsystem_cmake.write("\n  " + target.symbol)

            subsystem_cmake.write("\n)\n")

        subsystem_cmake.close()

    # Generates the top-level CMakeLists.txt
    def gen_top_level(self):
        output_path = "CMakeLists.txt"
        output_file = open(output_path, "w")

        output_file.write("cmake_minimum_required(VERSION 3.0)\n")
        output_file.write("project(CMSSW)\n\n")
        output_file.write("include_directories(${CMAKE_SOURCE_DIR})\n")
        output_file.write("include_directories(/usr/include/)\n")

        include_paths = set()

        for module in self.project.modules:
            for target in module.targets:
                include_paths |= target.include_dirs

        for d in include_paths:
            output_file.write("include_directories(" + d + ")\n")

        output_file.write("set(CMAKE_CXX_FLAGS \"${CMAKE_CXX_FLAGS} -pthread -march=native -Wno-attributes "
                          "-Wno-deprecated-declarations -Wno-deprecated-register -Wno-null-dereference -std=c++14\")\n")
        if cxxmodules:
            output_file.write("set(CMAKE_CXX_FLAGS \"${CMAKE_CXX_FLAGS} "
                              "-fmodules -Xclang -fmodules-local-submodule-visibility -Rmodule-build -ivfsoverlay " + prefix + "libs.overlay.yaml -fmodules-cache-path=${CMAKE_BINARY_DIR}/pcms/\")\n")

        subsystem_list = []
        for subsystem in self.project.subsystems:
            subsystem_list.append(subsystem)
        subsystem_list.sort()

        for subsystem in subsystem_list:
            output_file.write("add_subdirectory(" + subsystem + ")\n")
            subsystem_modules = self.project.subsystems[subsystem]
            self.handle_subsystem(subsystem, subsystem_modules)

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

# Dummy code in test normal dict generation doesn't work for some reason...
def make_dicts():
    for root, dirs, files in os.walk("."):
        if root.endswith("/src") and  "classes.h" in files:
            has_xml = ("classes_def.xml" in files)
            command = "genreflex classes.h -I" + os.getcwd()
            if has_xml:
                command += " -s classes_def.xml"
            print("Generating dict for " + root)
            subprocess.call(command, shell=True, cwd=root)

def main():
    #make_dicts()

    # Create an empty ScramProject
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





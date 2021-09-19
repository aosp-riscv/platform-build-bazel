#!/usr/bin/env python3
#
# Copyright (C) 2021 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A json-module-graph postprocessing script to generate a bp2build progress tracker.

Usage:
  ./bp2build-progress.py [report|graph] <module name>

Example:

  To generate a report on the `adbd` module, run:
    ./bp2build-progress report adbd

  To generate a graph on the `adbd` module, run:
    ./bp2build-progress graph adbd > graph.in && dot -Tpng -o graph.png graph.in

"""

import argparse
import os
import json
import subprocess
import collections

# This list of module types are omitted from the report and graph
# for brevity and simplicity. Presence in this list doesn't mean
# that they shouldn't be converted, but that they are not that useful
# to be recorded in the graph or report currently.
IGNORED_KINDS = set([
    "license_kind",
    "license",

    "cc_defaults",
    "cc_prebuilt_object",
    "cc_prebuilt_library_headers",
    "cc_prebuilt_library_shared",
    "cc_prebuilt_library_static",
    "cc_prebuilt_library_static",
    "cc_prebuilt_library",

    "ndk_prebuilt_static_stl",
    "ndk_library",
])

def generate_module_graph(module):
    src_root_dir = os.path.abspath(__file__ + "/../../../../..")
    result = subprocess.run(
        ["build/soong/soong_ui.bash", "--make-mode", "--skip-soong-tests", "json-module-graph"],
        capture_output=True,
        cwd = src_root_dir)
    result.check_returncode()

    result = subprocess.run(
        ["build/bazel/json_module_graph/query.sh", "fullTransitiveDeps", "out/soong/module-graph.json", module],
        cwd = src_root_dir,
        capture_output=True,
        encoding = "utf-8",
    )
    result.check_returncode()

    return json.loads(result.stdout)

def get_converted_modules():
    ret = []
    with open(os.path.join(os.path.dirname(__file__), "converted.txt")) as f:
        # Read line by line, excluding comments.
        # Each line is a module name.
        ret = [line.strip() for line in f.readlines() if not line.startswith("#")]
    return set(ret)

def get_os_variation(module):
    dep_variations = module.get("Variations")
    dep_variation_os = ""
    if dep_variations != None:
        dep_variation_os = dep_variations.get("os")
    return dep_variation_os

# Generate a dot file containing the transitive closure of the module.
def generate_dot_file(modules, converted, name_to_kind, module):
    DOT_TEMPLATE = """
digraph mygraph {{
  node [shape=box];

  %s
}}
"""

    make_node = lambda module, color: \
        '"%s\\n%s" [color=black, style=filled, fillcolor=%s]' % (module, name_to_kind.get(module), color)
    make_edge = lambda module, dep: \
        '"%s\\n%s" -> "%s\\n%s"' % (module, name_to_kind.get(module), dep, name_to_kind.get(dep))

    # Check that all modules in the argument are in the list of converted modules
    all_converted = lambda modules: all(map(lambda m: m in converted, modules))

    dot_entries = []

    for module, deps in modules.items():
        if module in converted:
            # Skip converted modules (nodes)
            continue
        elif module not in converted:
            if all_converted(deps):
                dot_entries.append(make_node(module, 'yellow'))
            else:
                dot_entries.append(make_node(module, 'tomato'))

        # Print all edges for this module
        for dep in list(deps):
            # Skip converted deps (edges)
            if dep not in converted:
                dot_entries.append(make_edge(module, dep))

    print(DOT_TEMPLATE % "\n  ".join(dot_entries))


# Generate a report for each module in the transitive closure, and the blockers for each module
def generate_report(modules, converted, name_to_kind, module):
    report_lines = []

    report_lines.append("bp2build report: %s" % module)
    report_lines.append("Ignored module types: %s" % IGNORED_KINDS)
    report_lines.append("Transitive dependency closure:")

    blocked_modules_report = collections.defaultdict(list)

    for module, deps in modules.items():
        unconverted_deps = list(filter(lambda dep: dep not in converted, list(deps)))
        unconverted_count = len(unconverted_deps)
        if module not in converted:
            report_entry = "%s [%s]: %s" % (module, name_to_kind.get(module), ", ".join(unconverted_deps))
            blocked_modules_report[unconverted_count].append(report_entry)

    for count, modules in reversed(sorted(blocked_modules_report.items())):
        report_lines.append("\n%d unconverted deps remaining:" % count)
        for module_string in modules:
            report_lines.append("  " + module_string)

    print("\n".join(report_lines))

def main():
    parser = argparse.ArgumentParser(description="")
    parser.add_argument("mode", help = "mode: graph or report")
    parser.add_argument("module", help = "name of Soong module")
    args = parser.parse_args()

    mode = args.mode
    module = args.module

    # The main module graph containing _all_ modules in the Soong build.
    module_graph = generate_module_graph(module)

    # The list of converted module names.
    converted = set(get_converted_modules())

    # The set of ignored modules. These modules are not shown in the graph or report.
    ignored = set()

    # A map of module name to its type/kind.
    name_to_kind = dict()

    # An adjacency list for all modules in the transitive closure, excluding ignored modules.
    module_adjacency_list = collections.defaultdict(set)

    # Do a single pass to find all top-level modules to be ignored
    for module in module_graph:
        if module["Type"] in IGNORED_KINDS:
            ignored.add(module["Name"])

        name_to_kind.setdefault(module["Name"], module["Type"])

    # Create the adjacency list.
    for module in module_graph:
        module_name = module["Name"]
        if module_name not in ignored:
            if get_os_variation(module) == "windows":
                # ignore the windows variations of modules
                continue
            # module_adjacency_list.setdefault(module_name, set())
            for dep in module["Deps"]:
                dep_name = dep["Name"]
                if dep_name not in ignored and dep_name != module_name:
                    module_adjacency_list[module_name].add(dep_name)

    if mode == "graph":
        generate_dot_file(module_adjacency_list, converted, name_to_kind, module)
    elif mode == "report":
        generate_report(module_adjacency_list, converted, name_to_kind, module)
    else:
        raise RuntimeError("unknown mode: %s" % mode)

if __name__ == "__main__":
    main()

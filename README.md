# scram2cmake

Creates CMake project files for SCRAM projects.

## Usage

`cd` into a directory containing a SCRAM project (like `cd ~/CERN/cmssw/`). Then run `PATH/TO/REPO/scram2cmake.py`. Done!

Now you should be able to use C++ IDE's like QtCreator to open the top-level `CMakeLists.txt`.

## Limitations

We only generate code for compiling all the packages of a SCRAM project (e.g. compiling `FWCore/ParameterSet/src|bin|test`).
Things like copying data files around, running tests, generating dictionaries etc. aren't supported at the moment.

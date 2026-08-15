import os
import sys

# Ensure trace_gc_crewai package root is on sys.path during pytest runs
package_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if package_root not in sys.path:
    sys.path.insert(0, package_root)

py_sources = txstratum/ $(wildcard *.py)
py_tests = tests/ $(wildcard *.py)

.PHONY: all
all: check tests

# testing:

tests_lib = ./tests/

pytest_flags = -p no:warnings --cov-report=term --cov-report=html --cov=txstratum
mypy_tests_flags = --show-error-code --warn-unused-configs --disallow-incomplete-defs --no-implicit-optional --warn-redundant-casts --strict-equality --disallow-subclassing-any --warn-return-any --disallow-untyped-decorators
mypy_sources_flags = --strict --show-error-code

#--disallow-any-generics
#--disallow-untyped-calls

#--warn-unused-ignores

#--disallow-untyped-defs
#--check-untyped-defs

#--implicit-reexport
#--no-implicit-reexport

.PHONY: tests-lib
tests-lib:
	pytest --durations=10 $(pytest_flags) --doctest-modules txstratum --cov-fail-under=70 $(tests_lib)

.PHONY: tests
tests: tests-lib

# checking:
#
.PHONY: mypy
mypy: mypy-sources mypy-tests

.PHONY: mypy-sources
mypy-sources: $(py_sources)
	mypy $(mypy_sources_flags) $^

.PHONY: mypy-tests
mypy-tests: $(py_tests)
	mypy $(mypy_tests_flags) $^

.PHONY: flake8
flake8: $(py_sources) $(py_tests)
	flake8 $^

.PHONY: isort-check
isort-check: $(py_sources) $(py_tests)
	isort --check-only $^

.PHONY: black-check
black-check:
	black --check .

.PHONY: check
check: flake8 black-check isort-check mypy

# formatting:

.PHONY: fmt
fmt: black isort

.PHONY: black
black:
	black .

.PHONY: isort
isort: $(py_sources) $(py_tests)
	isort --ac $^

# cleaning:

.PHONY: clean-pyc
clean-pyc:
	find txstratum tests -name \*.pyc -delete
	find txstratum tests -name __pycache__ -delete

.PHONY: clean
clean: clean-protos

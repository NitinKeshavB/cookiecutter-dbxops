# Execute the "targets" in this file with `make <target>` e.g. `make help`.
#
# You can also run multiple in sequence, e.g. `make clean lint test serve-coverage-report`
#
# See run.sh for more in-depth comments on what each target does.

build:
	bash run.sh build

check-skills:
	bash run.sh check-skills

check-placeholders:
	bash run.sh check-placeholders $(if $(STRICT),--strict,)

clean:
	bash run.sh clean

stage-framework:
	bash run.sh stage-framework

deploy-framework:
	bash run.sh deploy-framework $(TARGET) $(PROFILE)

validate-specs:
	bash run.sh validate-specs

metadata-dictionary:
	bash run.sh metadata-dictionary

upgrade-lakeflow:
	bash run.sh upgrade-lakeflow $(TAG)

check-upstream:
	bash run.sh check-upstream

new-bundle:
	bash run.sh new-bundle $(NAME) $(DOMAIN)

sync-substitutions:
	bash run.sh sync-substitutions $(if $(CHECK),--check,)

contracts:
	bash run.sh contracts $(if $(CHECK),--check,)

export-ontos:
	bash run.sh export-ontos $(TARGET)

recommend:
	bash run.sh recommend

render-platform:
	bash run.sh render-platform $(PROJECT)

help:
	bash run.sh help

install:
	bash run.sh install

lint:
	bash run.sh lint

lint-ci:
	bash run.sh lint:ci

onboard-check:
	bash run.sh onboard-check

publish-prod:
	bash run.sh publish:prod

publish-test:
	bash run.sh publish:test

release-prod:
	bash run.sh release:prod

release-test:
	bash run.sh release:test

serve-coverage-report:
	bash run.sh serve-coverage-report

sync-skills:
	bash run.sh sync-skills $(SOURCE)

test-ci:
	bash run.sh test:ci

test-quick:
	bash run.sh test:quick

test:
	bash run.sh run-tests

test-wheel-locally:
	bash run.sh test:wheel-locally

validate-domains:
	bash run.sh validate-domains

validate-metadata:
	bash run.sh validate-metadata

validate-registry:
	bash run.sh validate-registry $(REGISTRY) $(if $(STRICT),--strict,)

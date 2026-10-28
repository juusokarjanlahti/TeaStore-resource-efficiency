# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

TeaStore is a distributed micro-service reference application (Java/Jakarta EE) used for benchmarking, performance
modeling, and cloud/microservice management research. It emulates a tea webshop and consists of five independently
deployable services plus a registry, all built as a single Maven multi-module reactor.

## Build & test

Requires Maven 3.6+ and JDK 11 (`maven.compiler.source/target=11` in every service pom).

```bash
mvn clean install              # build all modules, run all tests, run checkstyle
mvn clean install -DskipTests  # build without tests (much faster, use for quick iteration)
```

- To build/test a single module, use Maven's reactor flags from the repo root, e.g.:
  `mvn -pl services/tools.descartes.teastore.image -am install`
- To run a single JUnit test class in one module: `mvn -pl services/tools.descartes.teastore.image -am test -Dtest=TestImageProvider -Dsurefire.failIfNoSpecifiedTests=false`. `-am` is required: without it the installed
  sibling POMs reference an unresolved `${teastoreversion}` parent and dependency resolution fails.
- On a newer JDK than 11 (e.g. 21), the Mockito-based tests fail with "Could not initialize plugin: MockMaker"
  because the bundled ByteBuddy predates that JDK. Use JDK 11, or add `-DargLine=-Dnet.bytebuddy.experimental=true`.
- Checkstyle (`limbo_checkstyle.xml`) runs in the `validate` phase for every module and is configured to fail the
  build on any warning (`violationSeverity=warning`, `failsOnError=true`). Fix style issues rather than suppressing
  them.
- Tests use JUnit 4 + Mockito; there is no integration-test harness inside the Maven build — service-to-service
  behavior is instead checked by the shell/e2e tooling below.

### Docker images

```bash
cd tools/
./build_docker.sh          # builds all service images + db + base image locally (tagged descartesresearch/*)
./build_docker.sh -p -r <registry>/   # buildx multi-arch build & push under a custom registry prefix
```

Each service module has its own `Dockerfile`; `mvn clean install` must be run first so the wars exist to `COPY` into
the images.

### Smoke / E2E tests

- `tools/test_webui.sh <host> <proto> <port>` — curl-based smoke test hitting a *running* WebUI (login, add-to-cart,
  category browsing, ads/recommender, images, `/status` endpoint).
- `tools/test_kieker.sh <host> <port>` — verifies Kieker/RabbitMQ monitoring traces are being produced.
- `e2e-tests/` — Cypress test suite (`npm install` then `npm run cy:run` / `cy:open`) against a live deployment.

There are no unit-test-level ways to exercise cross-service behavior; these scripts assume a deployed stack (see
Running locally, below).

## Running the stack locally

Simplest path for local development is Docker Compose, using the sample files in `examples/docker/`:

```bash
docker-compose -f examples/docker/docker-compose_default.yaml up -d   # no monitoring
docker-compose -f examples/docker/docker-compose_kieker.yaml up -d    # with Kieker/AMQP monitoring
```

WebUI is then at `http://localhost:8080/tools.descartes.teastore.webui/`. Kubernetes and Helm deployment options
also exist (`examples/kubernetes`, `examples/helm`) — see `GET_STARTED.md` for full details and environment variable
reference (`REGISTRY_HOST`, `REGISTRY_PORT`, `HOST_NAME`/`USE_POD_IP`, `SERVICE_PORT`, `DB_HOST`, `DB_PORT`,
`RECOMMENDER_ALGORITHM`, etc.).

## Architecture

### Module layout

```
interfaces/tools.descartes.teastore.entities/     shared DTOs (Product, Category, Order, OrderItem, User, ImageSize*, SessionBlob)
utilities/tools.descartes.teastore.registryclient/ service discovery + REST client + Ribbon load balancing (used by every service)
utilities/tools.descartes.teastore.kieker.*/       Kieker monitoring probes / RabbitMQ log transport
utilities/tools.descartes.teastore.database/       preconfigured MariaDB Docker image used as the persistence backing store
utilities/tools.descartes.teastore.dockerbase/     shared base Docker image for the services
services/tools.descartes.teastore.registry/        the service registry
services/tools.descartes.teastore.persistence/     JPA-backed data layer service
services/tools.descartes.teastore.auth/            authentication / session (SessionBlob) service
services/tools.descartes.teastore.recommender/     product recommendation service
services/tools.descartes.teastore.image/           product image generation, caching and storage service
services/tools.descartes.teastore.webui/           user-facing store front, aggregates the other services
```

Each `services/*` module is packaged as a `.war` (Jakarta EE servlet container) with the artifact name
`tools.descartes.teastore.<service>`, and follows the same internal package convention:
- `rest/` — JAX-RS (`jakarta.ws.rs`) REST endpoints, the service's external API
- `servlet/` or `startup/` — servlet lifecycle / app startup hooks (service registration happens here)
- service-specific domain packages, e.g. `persistence/domain` + `persistence/repository` (JPA entities/DAOs),
  `recommender/algorithm/impl/{cf,orderbased,pop}` (pluggable recommendation algorithms), `image/cache` +
  `image/storage` (pluggable cache-replacement and storage strategies), `auth/security`.

### Service discovery & communication

There is no API gateway. Services communicate directly over REST, using Netflix Ribbon for client-side load
balancing:
- `Service` (registryclient) is the enum of all services (`PERSISTENCE`, `RECOMMENDER`, `AUTH`, `WEBUI`, `IMAGE`),
  mapping to their web-app context name.
- `RegistryClient` (registryclient) is a singleton used by every service to register itself with the registry on
  startup, send periodic heartbeats, and resolve/load-balance calls to other services. Startup registration is driven
  by `StartupCallback`/`StartupCallbackTask`, invoked from each service's own `*Startup` class.
- Services are resilient to start-order: since each service polls/retries registration and lookups, any service can
  be started before its dependencies.
- WebUI is the only service with a browser-facing UI (JSP-style pages under `webapp/WEB-INF/pages`); it fans out to
  Auth, Persistence, Recommender and Image over REST to render pages. WebUI itself is *not* load-balanced by the
  stack — if you run multiple WebUI replicas you need your own front-end load balancer (`PROXY_NAME`/`PROXY_PORT`
  env vars exist for this).

### Monitoring

All services ship in a Kieker-instrumented variant (`utilities/tools.descartes.teastore.kieker.probes`). Traces can
be shipped to an AMQP/RabbitMQ collector (`RABBITMQ_HOST` env var, `kieker.rabbitmq` module) or written to a local
file (`LOG_TO_FILE=true`), with optional method-parameter logging (`LOG_PARAMETERS=true`).

## Code style

- Java 11, checkstyle-enforced (see `limbo_checkstyle.xml`): tab width 4, Javadoc required on package-scope+ methods,
  types (with `@author`) and variables, no star imports, no unused imports, standard naming conventions.
- Apache License 2.0 header is present at the top of essentially every source file — keep it when adding new files
  in existing modules.

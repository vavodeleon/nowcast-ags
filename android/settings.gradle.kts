pluginManagement {
    repositories { google(); mavenCentral(); gradlePluginPortal() }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories { google(); mavenCentral() }
}
rootProject.name = "nowcast-ags"
// common: leer reloj.json, guardarlo, colores y textos. Lo comparten el
// celular y el reloj para que los dos digan exactamente lo mismo.
include(":common", ":app", ":wear")

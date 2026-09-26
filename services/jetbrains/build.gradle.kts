import org.jetbrains.intellij.platform.gradle.IntelliJPlatformType
import org.jetbrains.intellij.platform.gradle.TestFrameworkType
import org.jetbrains.intellij.platform.gradle.tasks.VerifyPluginTask
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("java")
    id("org.jetbrains.kotlin.jvm") version "2.3.21"
    id("org.jetbrains.kotlin.plugin.serialization") version "2.3.21"
    id("org.jetbrains.intellij.platform") version "2.18.1"
}

group = "com.terraducktel"
version = "0.1.0"

repositories {
    mavenCentral()
    intellijPlatform { defaultRepositories() }
}

dependencies {
    intellijPlatform {
        intellijIdea("2026.1")
        testFramework(TestFrameworkType.Platform)
    }
    testImplementation("junit:junit:4.13.2")
}

intellijPlatform {
    pluginConfiguration {
        id = "com.terraducktel.jetbrains"
        name = "Terraducktel"
        version = project.version.toString()
        vendor { name = "Terraducktel" }
        ideaVersion {
            sinceBuild = "261"
            untilBuild = provider { null }
        }
    }
    buildSearchableOptions = false
    // `make verify-jetbrains` -> `verifyPlugin`. Downloads each listed IDE (cached under
    // ~/.gradle/caches after the first run) and checks the built plugin against it for API usages
    // that don't exist / are deprecated / are scheduled for removal in that IDE.
    pluginVerification {
        ides {
            create(IntelliJPlatformType.IntellijIdea, "2026.1")
            recommended()
        }
        // The Gradle plugin's own default failure levels are ALL of them, which would fail this
        // build on informational findings the task brief treats as acceptable (deprecated /
        // experimental / internal API usage) — not just on a real compatibility problem (a call to
        // an API that doesn't exist in the target IDE). We fail only on that: SignInFlow.pickMode's
        // AppMode.isRemoteDevHost() check (there is no public API for "is this a Remote Development
        // host") and the platform's own deprecations (StatusBarWidget.MultipleTextValuesPresentation,
        // ToolWindowFactory.isApplicable/isDoNotActivateOnStart, SimpleListCellRenderer.create) are
        // real, worth watching, and listed in `make verify-jetbrains`'s output — just not build-fatal.
        failureLevel = listOf(VerifyPluginTask.FailureLevel.COMPATIBILITY_PROBLEMS)
    }
}

kotlin { compilerOptions { jvmTarget = JvmTarget.JVM_21 } }
tasks.withType<JavaCompile> { options.release = 21 }
tasks.test { useJUnit(); testLogging { events("failed"); exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL } }

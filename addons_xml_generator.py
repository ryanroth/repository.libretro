# *
# *  Copyright (C) 2012-2013 Garrett Brown
# *  Copyright (C) 2010      j48antialias
# *
# *  This Program is free software; you can redistribute it and/or modify
# *  it under the terms of the GNU General Public License as published by
# *  the Free Software Foundation; either version 2, or (at your option)
# *  any later version.
# *
# *  This Program is distributed in the hope that it will be useful,
# *  but WITHOUT ANY WARRANTY; without even the implied warranty of
# *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# *  GNU General Public License for more details.
# *
# *  You should have received a copy of the GNU General Public License
# *  along with XBMC; see the file COPYING.  If not, write to
# *  the Free Software Foundation, 675 Mass Ave, Cambridge, MA 02139, USA.
# *  http://www.gnu.org/copyleft/gpl.html
# *
# *  Based on code by j48antialias:
# *  https://anarchintosh-projects.googlecode.com/files/addons_xml_generator.py

""" addons.xml generator """

import inspect
import os
import re
import sys
import shutil
import xml.etree.ElementTree as ET
import zipfile

try:
    import md5
except ImportError:
    import hashlib

# Compatibility with 3.0, 3.1 and 3.2 not supporting u"" literals
if sys.version < '3':
    import codecs
    def u(x):
        return codecs.unicode_escape_decode(x)[0]
else:
    def u(x):
        return x

ADDON_XML = 'addon.xml'
CHANGELOG_TXT = 'changelog.txt'
CHANGELOG_VERSION_TXT = 'changelog-%s.txt'
ICON_PNG = 'icon.png'
FANART_JPG = 'fanart.jpg'
ADDONS_XML = 'addons.xml'

class Version:
    """
    Parse a version folder's meta.xml and store the entries as a flat dict\
    """
    def __init__(self, version, versionPath, addon):
        self.version    = version
        self.path       = versionPath
        self.properties = { }

        metaPath = os.path.join(self.path, 'meta.xml')
        if not os.path.exists(metaPath):
            raise Exception('no meta xml: %s' % metaPath)
        tree = ET.parse(metaPath)
        properties = tree.getroot().getchildren()
        for prop in properties:
            # Store an empty string instead of None if element is empty
            self.properties[prop.tag] = prop.text if prop.text else ''

        self.addProperties(addon.addonId, addon.platform)

    def getVersion(self):
        return self.version

    def getProperties(self):
        return self.properties

    def getLibrary(self):
        for platform in ['library_android', 'library_linux', 'library_osx', 'library_win']:
            if self.properties[platform]:
                return self.properties[platform]
        return None

    def addProperties(self, addonId, platform):
        """
        Infer additional properties about the add-on
        """
        self.properties['version'] = self.version

        self.properties['id'] = addonId

        # extensions is a pipe-separated list of extensions (no leading dot)
        # supported_files is a comma-separated list of extensions (with leading dot)
        extensions = self.properties.get('extensions', '').split('|')
        self.properties['supported_files'] = ', '.join(['.' + ext for ext in extensions if ext])

        # arch appends " (32-bit)" to names under 32-bit linux
        self.properties['arch'] = ' (32-bit)' if platform == 'linux32' else ''

        # Extract library name
        library = None
        files = os.listdir(self.path)
        for f in files:
            if f[-4:] == '.xml':
                continue
            # First non-xml file found is the assumed library
            library = f
            break
        if not library:
            raise Exception('no non-xml files found at %s' % self.path)
        self.properties['library_android'] = ''
        self.properties['library_linux'] = library if platform in ['linux', 'linux32'] else ''
        self.properties['library_osx'] = ''
        self.properties['library_win'] = library if platform == 'win32' else ''

        # broken property, if exists, needs to be surrounded by tags
        broken = self.properties.get('broken')
        if broken:
            self.properties['broken'] = '<broken>%s</broken>' % broken

        # Set the add-on platform tag
        platformTable = {
            'win32':   'windx wingl',
            'linux':   'linux',
            'linux32': 'linux',
        }
        self.properties['platform'] = platformTable[platform]


class Addon:
    """
    Representation of an addon. Encompases addon.xml, changelog.txt,
    icon.png, fanart.jpg, and all other files. Output is an archive, the
    changelog at that version, an md5 hash of the archive, and an updated
    icon.png and fanart.png if changed.
    """
    def __init__(self, addonId, path, platform):
        self.addonId  = addonId
        self.path     = path
        self.platform = platform
        self.versions = { }

        files = os.listdir(self.path)
        if ADDON_XML not in files:
            raise Exception("%s doesn't have an addon.xml file" % self.addonId)

        with open(os.path.join(self.path, ADDON_XML)) as f:
            self.addonTemplate = f.read().splitlines(False) # Don't keep line endings

        # Currently unused
        self.icon = os.path.join(self.path, ICON_PNG) if ICON_PNG in files else None
        self.fanart = os.path.join(self.path, FANART_JPG) if FANART_JPG in files else None

        versions = []
        platformPath = os.path.join(self.path, self.platform)
        if os.path.exists(platformPath):
            versions = os.listdir(platformPath)
        if not versions:
            raise Exception('no clients: %s' % self.addonId)

        for version in versions:
            versionPath = os.path.join(platformPath, version)
            try:
                self.versions[version] = Version(version, versionPath, self)
            except Exception as e:
                raise Exception('exception:  %s: %s' % (self.addonId, str(e)))

    def getVersions(self):
        return sorted(self.versions.keys())

    def getLibrary(self, version):
        return self.versions[version].getLibrary()

    def getLibraryPath(self, version):
        return os.path.join(self.path, self.platform, version, self.getLibrary(version))

    def getAddonXml(self, version):
        props = self.versions[version].getProperties()
        return '\n'.join([self.replaceTokens(line, props) for line in self.addonTemplate])

    def replaceTokens(self, line, props):
        skip = 0
        # Validate on XML identifiers (assuming ASCII characters)
        validToken = re.compile('^[A-Za-z:_][A-Za-z0-9:_.-]*$')
        while line.count('@', skip) >= 2:
            i1 = line.find('@') + 1
            i2 = line.find('@', i1)
            token = line[i1:i2]
            # Make sure that discovered token is a valid XML identifier (no spaces, etc)
            if validToken.match(token):
                line = line[:i1 - 1] + props.get(token, '') + line[i2 + 1:]
                skip = i1 - 1 # Rewind 1 character so that tokens can be recursive
            else:
                skip = i1
        return line

    def getChangelog(self, version):
        # [B]2013-02-16[/B] Version [B]1.0.0[/B]
        # . Initial release. https://github.com/libretro/bnes-libretro @ b735dca (2012-04-14)
        CHANGELOG_ENTRY = '[B]%(date)s[/B] Version [B]%(version)s[/B]\n. %(changelog)s'

        # Make sure that version exists in self.versions
        self.versions[version]

        # Previous versions are up-to-and-including version
        previousVersions = []
        for v in self.getVersions():
            previousVersions.append(self.versions[v])
            if v == version:
                break
        previousVersions.reverse()
        return '\n\n'.join([CHANGELOG_ENTRY % v.getProperties() for v in previousVersions])

"""
class OldAddon:
    def x(self):
        self.isaddon = True
        with open(os.path.join(self.path, ADDON_XML), 'r') as f:
            xml_lines = f.read().splitlines(False) # Don't keep line endings
        for line in xml_lines:
            # skip encoding format line
            if line.startswith('<?xml'): continue
            # add line
            if sys.version < '3':
                self.addon_xml += unicode(line.rstrip() + '\n', 'UTF-8')
            else:
                self.addon_xml += line.rstrip() + '\n'
"""

class Generator:
    def __init__(self, platform):
        # Repository index
        addonsXml = u('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n')

        cd = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))

        # Add-on zips go in this folder
        releasesDir = os.path.join(cd, 'release', platform)
        if not os.path.exists(releasesDir):
            os.makedirs(releasesDir)

        # loop through and process each addon
        addonsDir = os.path.join(cd, 'addons')
        addons = sorted(os.listdir(addonsDir))
        for addonId in addons:
            addonDir = os.path.join(addonsDir, addonId)
            releaseDir = os.path.join(releasesDir, addonId)
            if not os.path.exists(releaseDir):
                os.makedirs(releaseDir)

            # skip any file or .svn folder or .git folder
            if (not os.path.isdir(addonDir) or addonId == '.svn' or addonId == '.git' or '.' not in addonId):
                continue

            try:
                if addonId == 'gameclient.quicknes':
                    pass
                addon = Addon(addonId, addonDir, platform)
            except Exception as e:
                print(str(e))
                continue

            # Copy icon.png and fanart.jpg
            for art in [addon.icon, addon.fanart]:
                if art:
                    shutil.copy2(art, releaseDir)

            # Process each add-on version
            versions = addon.getVersions()
            mostRecentVersion = versions[-1]
            for version in versions:
                zipname = os.path.join(releaseDir, '%s-%s.zip' % (addonId, version))

                addonXml = addon.getAddonXml(version)
                changelog = addon.getChangelog(version)

                library = addon.getLibrary(version)
                libraryPath = addon.getLibraryPath(version)

                # Append addon.xml to addons.xml
                if version == mostRecentVersion:
                    if addonXml.startswith('<?xml'):
                        addonsXml += ''.join(addonXml.splitlines(True)[1:]).rstrip() + '\n\n'
                    else:
                        addonsXml += addonXml.rstrip() + '\n\n'

                # If the zip already exists, compare addon.xml, changelog.txt and artwork for modifications
                if zipfile.is_zipfile(zipname):
                    modified = self.compareZip(zipname, addonId, addonXml, changelog, addon.icon, addon.fanart)
                    if not modified:
                        print('unmodified: %s v%s' % (addonId, version))
                        continue
                    else:
                        os.remove(zipname)
                elif os.path.isfile(zipname):
                    os.remove(zipname)
                elif os.path.isdir(zipname):
                    shutil.rmtree(zipname)

                # Shove everything into the zip file
                with zipfile.ZipFile(zipname, 'w', zipfile.ZIP_DEFLATED) as myzip:
                    myzip.writestr(os.path.join(addonId, ADDON_XML), addonXml)
                    myzip.writestr(os.path.join(addonId, CHANGELOG_TXT), changelog)
                    myzip.write(libraryPath, os.path.join(addonId, library))
                    if addon.icon:
                        myzip.write(addon.icon, os.path.join(addonId, ICON_PNG))
                    if addon.fanart:
                        myzip.write(addon.fanart, os.path.join(addonId, FANART_JPG))

                # Create md5 file and changelog-version.txt
                self.makeMd5(zipname)
                with open(os.path.join(releaseDir, CHANGELOG_VERSION_TXT % version), 'w') as f:
                    f.write(changelog)

                # Pad with spaces to match length of "unmodified" above
                print('packaged:   %s v%s' % (addonId, version))

        # Now deal with the repositories
        reposDir = os.path.join(cd, 'repositories')
        # Small adjustment: package linux32 repo under linux releases so that
        # 64-bit versions can install the 32-bit repo
        if platform == 'win32':
            platforms = ['win32']
        elif platform == 'linux':
            platforms = ['linux', 'linux32']
        else:
            platforms = []
        for p in platforms: 
            repoId = 'repository.libretro-%s' % p
            repoDir = os.path.join(reposDir, repoId)
            releaseDir = os.path.join(releasesDir, repoId)
            if not os.path.exists(releaseDir):
                os.makedirs(releaseDir)

            # Read in addon.xml file
            with open(os.path.join(repoDir, ADDON_XML)) as f:
                repoXml = '\n'.join(f.read().splitlines(False))

            # Copy icon.png and fanart.jpg
            for art in [os.path.join(repoDir, ICON_PNG), os.path.join(repoDir, FANART_JPG)]:
                if os.path.exists(art):
                    shutil.copy2(art, releaseDir)

            # Create zip
            version = '1.0.2' # So that XBMC will automatically update to the new structure
            zipname = os.path.join(releaseDir, '%s-%s.zip' % (repoId, version))
            # If the zip already exists, compare addon.xml files
            if zipfile.is_zipfile(zipname):
                modified = self.compareZip(zipname, repoId, repoXml, None, None, None)

                # Zip file is now closed
                if not modified:
                    print('unmodified: %s v%s' % (repoId, version))
                    continue
                else:
                    os.remove(zipname)
            elif os.path.isfile(zipname):
                os.remove(zipname)
            elif os.path.isdir(zipname):
                shutil.rmtree(zipname)

            with zipfile.ZipFile(zipname, 'w', zipfile.ZIP_DEFLATED) as myzip:
                myzip.writestr(os.path.join(repoId, ADDON_XML), repoXml)
                if os.path.exists(os.path.join(repoDir, CHANGELOG_TXT)):
                    myzip.write(os.path.join(repoDir, CHANGELOG_TXT), os.path.join(repoId, CHANGELOG_TXT))
                if os.path.exists(os.path.join(repoDir, ICON_PNG)):
                    myzip.write(os.path.join(repoDir, ICON_PNG), os.path.join(repoId, ICON_PNG))
                if os.path.exists(os.path.join(repoDir, FANART_JPG)):
                    myzip.write(os.path.join(repoDir, FANART_JPG), os.path.join(repoId, FANART_JPG))
            self.makeMd5(zipname)

            # Remove xml header if it is present
            if repoXml.startswith('<?xml'):
                repoXml = ''.join(repoXml.splitlines(True)[1:])

            # Don't forget to update addons.xml
            addonsXml += repoXml.rstrip() + '\n\n'

            print('packaged:   %s v%s' % (repoId, version))

        # Finally, write the addons.xml file and addons.xml.md5
        addonsXml = addonsXml.strip() + u('\n</addons>\n')
        with open(os.path.join(releasesDir, ADDONS_XML), 'w') as f:
            f.write(addonsXml)
        """
        try:
            open(ADDONS_XML, 'wt', encoding='UTF-8').write(addonsXml)
        except TypeError:
            open(ADDONS_XML, 'wb').write(addonsXml)
        """

        self.makeMd5(os.path.join(releasesDir, ADDONS_XML))

        print('Finished updating addons xml and md5 files for %s' % platform)

    def makeMd5(self, filepath):
        try:
            m = hashlib.md5(open(filepath, 'rb').read()).hexdigest()
            open(filepath + '.md5', 'wt', encoding='UTF-8').write(m)
        except NameError:
            m = md5.new(open(filepath, 'rb').read()).hexdigest()
            open(filepath + '.md5', 'wb').write(m)

    def compareZip(self, zipname, addonId, addonXml, changelog, icon, fanart):
        different = False
        with zipfile.ZipFile(zipname, 'r') as myzip:
            foundChangelog = False
            foundIcon = False
            foundFanart = False
            for filepath in myzip.namelist():
                # Strip leading folder (assuming it is named addonId)
                filename = filepath[len(addonId) + 1:]

                # Check if addon.xml is different
                if filename == ADDON_XML:
                    different = (myzip.read(filepath) != addonXml)
                # Check if changelog.txt is different
                elif filename == CHANGELOG_TXT:
                    foundChangelog = True
                    different = (myzip.read(filepath) != changelog)
                # Check if icon.png exists in myzip and addonDir (no difference check)
                # TODO: Check size
                elif filename == ICON_PNG:
                    foundIcon = True
                    different = (not bool(icon))
                # Check if fanart.jpg exists in myzip and addonDir (no difference check)
                # TODO: Check size
                elif filename == FANART_JPG:
                    foundFanart = True
                    different = (not bool(fanart))
                # TODO: Also check library
                if different:
                    break
            if not different:
                if (not foundIcon and icon) or (not foundFanart and fanart) or (not foundChangelog and changelog):
                    different = True
            return different


if __name__ == '__main__':
    # First argument is the platform name
    if len(sys.argv) >= 2:
        platform = sys.argv[1]
        Generator(platform)
    else:
        # If no arguments were specified, run for all platforms
        for platform in ['win32', 'linux', 'linux32']:
            print('Platform %s' % platform)
            Generator(platform)

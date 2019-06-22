"""
/***************************************************************************
 QuickOSM
 A QGIS plugin
 OSM Overpass API frontend
                             -------------------
        begin                : 2014-06-11
        copyright            : (C) 2014 by 3Liz
        email                : info at 3liz dot com
        contributor          : Etienne Trimaille
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""
import io
import logging
from os.path import split, join, isfile
from sys import exc_info

from QuickOSM.core.utilities.tools import tr, quickosm_user_folder
from QuickOSM.core.utilities.utilities_qgis import display_message_bar
from QuickOSM.definitions.osm import LayerType
from qgis.PyQt.QtWidgets import QWidget, QApplication, QCompleter
from qgis.core import (
    QgsGeometry,
    QgsCoordinateTransform,
    QgsCoordinateReferenceSystem,
    QgsProject,
    Qgis,
)
from qgis.gui import QgsFileWidget
from qgis.utils import iface

LOGGER = logging.getLogger('QuickOSM')


class QuickOSMWidget(QWidget):
    def __init__(self, parent=None):
        self.last_places = []
        self.last_nominatim_places_filepath = join(
            quickosm_user_folder(),
            'nominatim.txt')
        QWidget.__init__(self, parent)

    def init(self):
        """Init after the UI is loaded."""
        self.output_directory.lineEdit().setPlaceholderText(
            tr('Save to temporary file'))
        self.output_directory.setStorageMode(QgsFileWidget.GetDirectory)
        self.output_directory.setDialogTitle(tr('Select a directory'))
        self.output_directory.fileChanged.connect(self.disable_prefix_file)

        try:
            self.advanced.setSaveCollapsedState(False)
            self.advanced.setCollapsed(True)
        except AttributeError:
            # OSM File widget does not have this QgsGroupBox
            pass

    def init_nominatim_autofill(self):
        """Open the nominatim file and start setting up the completion."""
        # Useful to avoid duplicate if we add a new completer.
        self.lineEdit_nominatim.setCompleter(None)
        self.last_places = []

        if isfile(self.last_nominatim_places_filepath):
            with io.open(
                    self.last_nominatim_places_filepath,
                    'r',
                    encoding='utf8') as f:
                for line in f:
                    self.last_places.append(line.rstrip('\n'))

            nominatim_completer = QCompleter(self.last_places)
            self.lineEdit_nominatim.setCompleter(nominatim_completer)
            self.lineEdit_nominatim.completer().setCompletionMode(
                QCompleter.PopupCompletion)
        else:
            io.open(self.last_nominatim_places_filepath, 'a').close()

    @staticmethod
    def sort_nominatim_places(existing_places, place):
        if place in existing_places:
            existing_places.pop(existing_places.index(place))
        existing_places.insert(0, place)
        return existing_places[:10]

    def nominatim_value(self):
        """Edit the new nominatim file."""
        value = self.lineEdit_nominatim.text()
        new_list = self.sort_nominatim_places(self.last_places, value)

        try:
            with io.open(
                    self.last_nominatim_places_filepath,
                    'w',
                    encoding='utf8') as f:
                for item in new_list:
                    f.write('{}\n'.format(item))
        except UnicodeDecodeError:
            # The file is corrupted ?
            # Remove all old places
            with io.open(
                    self.last_nominatim_places_filepath,
                    'w',
                    encoding='utf8') as f:
                f.write('\n')

        self.init_nominatim_autofill()

        return value

    def disable_prefix_file(self):
        """
        If the directory is empty, we disable the file prefix
        """
        if self.output_directory.filePath():
            self.lineEdit_filePrefix.setDisabled(False)
        else:
            self.lineEdit_filePrefix.setText("")
            self.lineEdit_filePrefix.setDisabled(True)

    def query_type_updated(self):
        """Enable/disable the extent widget."""
        current = self.cb_query_type.currentData()

        if self.cb_query_type.count() == 2:
            # Query tab
            self.comboBox_extentLayer.setVisible(current == 'layer')
        else:
            # Quick query tab
            if current in ['in', 'around']:
                self.stacked_query_type.setCurrentIndex(0)
                self.spinBox_distance_point.setVisible(current == 'around')
            elif current in ['layer']:
                self.stacked_query_type.setCurrentIndex(1)
            elif current in ['canvas', 'attributes']:
                self.stacked_query_type.setCurrentIndex(2)

    def get_output_geometry_types(self):
        """
        Get all checkbox about outputs and return a list

        @rtype: list
        @return: list of layers
        """
        output_geom_types = []
        if self.checkBox_points.isChecked():
            output_geom_types.append(LayerType.Points)
        if self.checkBox_lines.isChecked():
            output_geom_types.append(LayerType.Lines)
        if self.checkBox_multilinestrings.isChecked():
            output_geom_types.append(LayerType.Multilinestrings)
        if self.checkBox_multipolygons.isChecked():
            output_geom_types.append(LayerType.Multipolygons)

        return output_geom_types

    def get_white_list_values(self):
        """
        Get all line edits about columns for each layers and return a dic

        @rtype: dic
        @return: doc of layers with columns
        """
        white_list_values = {}
        if self.checkBox_points.isChecked():
            white_list_values[LayerType.Points] = (
                self.lineEdit_csv_points.text())
        if self.checkBox_lines.isChecked():
            white_list_values[LayerType.Lines] = (
                self.lineEdit_csv_lines.text())
        if self.checkBox_multilinestrings.isChecked():
            white_list_values[LayerType.Multilinestrings] = (
                self.lineEdit_csv_multilinestrings.text())
        if self.checkBox_multipolygons.isChecked():
            white_list_values[LayerType.Multipolygons] = (
                self.lineEdit_csv_multipolygons.text())

        return white_list_values

    def get_bounding_box(self):
        """
        Get the geometry of the bbox in WGS84

        @rtype: QGsRectangle in WGS84
        @return: the extent of the map canvas
        """
        query_type = self.cb_query_type.currentData()

        if query_type == 'canvas':
            geom_extent = iface.mapCanvas().extent()
            source_crs = iface.mapCanvas().mapSettings().destinationCrs()
        else:
            # Else if a layer is checked
            layer = self.comboBox_extentLayer.currentLayer()
            geom_extent = layer.extent()
            source_crs = layer.crs()

        geom_extent = QgsGeometry.fromRect(geom_extent)
        epsg_4326 = QgsCoordinateReferenceSystem('EPSG:4326')
        crs_transform = QgsCoordinateTransform(
            source_crs, epsg_4326, QgsProject.instance())
        geom_extent.transform(crs_transform)
        return geom_extent.boundingBox()

    def start_process(self):
        """
        Make some stuff before launching the process
        """
        self.pushButton_runQuery.setDisabled(True)
        self.pushButton_runQuery.initialText = self.pushButton_runQuery.text()
        self.pushButton_runQuery.setText(tr('Running query ...'))
        self.progressBar_execution.setMinimum(0)
        self.progressBar_execution.setMaximum(0)
        self.progressBar_execution.setValue(0)
        self.label_progress.setText('')

    def end_process(self):
        """
        Make some stuff after the process
        """
        self.pushButton_runQuery.setDisabled(False)
        self.pushButton_runQuery.setText(self.pushButton_runQuery.initialText)
        self.progressBar_execution.setMinimum(0)
        self.progressBar_execution.setMaximum(100)
        self.progressBar_execution.setValue(100)
        QApplication.processEvents()

    def set_progress_percentage(self, percent):
        """
        Slot to update percentage during process
        """
        self.progressBar_execution.setValue(percent)
        QApplication.processEvents()

    def set_progress_text(self, text):
        """
        Slot to update text during process
        """
        self.label_progress.setText(text)
        QApplication.processEvents()

    def display_geo_algorithm_exception(self, e):
        """
        Display quickosm exceptions
        """
        self.label_progress.setText("")
        LOGGER.debug(e.msg)
        display_message_bar(e.msg, level=e.level, duration=e.duration)

    @staticmethod
    def display_exception(e):
        """
        Display others exceptions
        """
        exc_type, _, exc_tb = exc_info()
        f_name = split(exc_tb.tb_frame.f_code.co_filename)[1]
        _, _, tb = exc_info()
        import traceback
        traceback.print_tb(tb)
        LOGGER.critical(
            tr('A critical error occurred, this is the traceback:'))
        LOGGER.critical(exc_type)
        LOGGER.critical(f_name)
        LOGGER.critical(e)
        LOGGER.critical('\n'.join(traceback.format_tb(tb)))

        display_message_bar(
            tr('Error in the logs, QuickOSM panel, please report it to '
               'GitHub'),
            level=Qgis.Critical,
            open_logs=True,
            duration=10)

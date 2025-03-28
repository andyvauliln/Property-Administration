{% extends "_base.html" %}
{% load slippers %}
{% load custom_filters %}
{% block content %}

<section class="bg-white dark:bg-gray-900"> 
    <div class="pb-8 px-4 mx-auto max-w-screen-xl lg:pb-16 lg:px-6">
        <div class="mx-auto max-w-screen-md mb-8 lg:mb-12 flex justify-center items-center flex-col">
            <h2 class="text-4xl tracking-tight font-extrabold text-gray-900 dark:text-white mr-4 mt-4">Parking Calendar</h2>
        </div>
        
        <!-- Add building sections -->
        <div class="grid gap-8" id="buildingsContainer">
            <!-- Buildings will be populated by JavaScript -->
        </div>
    </div>
</section>

<script>
    console.log('Raw parking_data_json:', {{ parking_data_json|safe }});
    const parkingData = JSON.parse('{{ parking_data_json|safe }}');
    console.log('Grouped parkingData:', parkingData);
    const model_fields_json = JSON.parse('{{ model_fields_json|safe }}');
    console.log('Model fields:', model_fields_json);
    function getParkingById(id) {
        for (const [buildingNumber, parkings] of Object.entries(parkingData)) {
            const found = parkings.find(p => p.id === id);
            if (found) {
                return found;
            }
        }
        return null;
    }

    function displayParkingInfo(parking) {
        if (!parking) return '';
        
        return `
            <div class="space-y-4">
                <div class="grid grid-cols-2 gap-4">
                    <div>
                        <p class="text-sm font-medium text-gray-500">Building</p>
                        <p class="text-sm text-gray-900">${parking.building_n}</p>
                    </div>
                    <div>
                        <p class="text-sm font-medium text-gray-500">Apartment</p>
                        <p class="text-sm text-gray-900">${parking.apartment_number}</p>
                    </div>
                    <div>
                        <p class="text-sm font-medium text-gray-500">Status</p>
                        <p class="text-sm text-gray-900">${parking.status}</p>
                    </div>
                    <div>
                        <p class="text-sm font-medium text-gray-500">Tenant</p>
                        <p class="text-sm text-gray-900">${parking.tenant_name || 'N/A'}</p>
                    </div>
                    <div>
                        <p class="text-sm font-medium text-gray-500">Phone</p>
                        <p class="text-sm text-gray-900">${parking.tenant_phone || 'N/A'}</p>
                    </div>
                    <div>
                        <p class="text-sm font-medium text-gray-500">Dates</p>
                        <p class="text-sm text-gray-900">${parking.start_date ? formatDates(parking.start_date, parking.end_date) : 'N/A'}</p>
                    </div>
                </div>
                <div>
                    <p class="text-sm font-medium text-gray-500">Notes</p>
                    <p class="text-sm text-gray-900">${parking.notes || 'No notes'}</p>
                </div>
            </div>
        `;
    }

    function handleBookingChange(bookingId) {
        const bookingField = document.getElementById('booking');
        const startDateField = document.getElementById('start_date');
        const endDateField = document.getElementById('end_date');
        const statusField = document.getElementById('status');
        const dateFields = document.querySelectorAll('.date-field');
        
        if (bookingId) {
            // Find the booking in the dropdown options
            const bookingOption = bookingField.querySelector(`option[value="${bookingId}"]`);
            if (bookingOption) {
                const label = bookingOption.textContent;
                // Extract dates from the label format "ApartmentName:[YYYY-MM-DD - YYYY-MM-DD]"
                const dateMatch = label.match(/\[(.*?) - (.*?)\]/);
                if (dateMatch) {
                    startDateField.value = dateMatch[1];
                    endDateField.value = dateMatch[2];
                    statusField.value = 'Booked';
                    
                    // Hide date fields and make them readonly when booking is selected
                    dateFields.forEach(field => {
                        field.style.display = 'none';
                        field.querySelector('input').readOnly = true;
                    });
                }
            }
        } else {
            // Clear dates if no booking is selected
            startDateField.value = '';
            endDateField.value = '';
            startDateField.readOnly = false;
            endDateField.readOnly = false;
            
            // Show/hide date fields based on status
            handleStatusChange(statusField.value);
        }
    }

    function createFormFields(modelFields, parkingData) {
        return modelFields.map(field => {
            const value = parkingData[field.name] || '';
            let inputElement = '';
            const isRequired = field.name === 'apartment_number' || field.name === 'parking_number';
            console.log(field, "field");
            console.log(value, "value");
            console.log(parkingData, "parkingData");
            console.log(field.ui_element, "field.ui_element");
            switch (field.ui_element) {
                case 'dropdown':
                    const options = field.choices.map(choice => {
                        const selected = choice.value === value ? 'selected' : '';
                        return `<option value="${choice.value}" ${selected}>${choice.label}</option>`;
                    }).join('');
                    
                    // Add onchange events for specific fields
                    const extraAttributes = field.name === 'apartment_number' ? 
                        'onchange="filterBookingsByApartment(this.value)"' : 
                        field.name === 'status' ?
                        'onchange="handleStatusChange(this.value)"' :
                        field.name === 'booking' ?
                        'onchange="handleBookingChange(this.value)"' : '';
                    
                    inputElement = `
                        <select id="${field.name}" name="${field.name}" 
                                class="bg-gray-50 border border-gray-300 text-gray-900 text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block w-full p-2.5 dark:bg-gray-700 dark:border-gray-600 dark:placeholder-gray-400 dark:text-white"
                                ${isRequired ? 'required' : ''}
                                ${extraAttributes}>
                            <option value="">Select ${field.label}</option>
                            ${options}
                        </select>`;
                    break;
                    
                case 'textarea':
                    inputElement = `
                        <textarea id="${field.name}" name="${field.name}" 
                                 class="bg-gray-50 border border-gray-300 text-gray-900 text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block w-full p-2.5 dark:bg-gray-700 dark:border-gray-600 dark:placeholder-gray-400 dark:text-white"
                                 ${isRequired ? 'required' : ''}>${value}</textarea>`;
                    break;

                case 'datepicker':
                    // Format the date value if it exists
                    console.log(field.name, value, "DATA PICKER");
                    let formattedValue = value;
                    if (value) {
                        try {
                            const date = new Date(value);
                            formattedValue = date.toISOString().split('T')[0];
                        } catch (e) {
                            console.error('Error formatting date:', e);
                        }
                    }
                    console.log(formattedValue, "formattedValue");
                    inputElement = `
                        <input datepicker datepicker-format="MM dd yyyy" type="text" id="${field.name}" name="${field.name}" 
                               value="${formattedValue || ''}"
                               class="bg-gray-50 border border-gray-300 text-gray-900 text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block w-full p-2.5 dark:bg-gray-700 dark:border-gray-600 dark:placeholder-gray-400 dark:text-white dark:focus:ring-blue-500 dark:focus:border-blue-500" placeholder="Select date"
                               ${isRequired ? 'required' : ''}>`;
                    console.log(inputElement, "inputElement");
                    break;
                    
                default: // 'input'
                    inputElement = `
                        <input type="text" id="${field.name}" name="${field.name}" 
                               value="${value}"
                               class="bg-gray-50 border border-gray-300 text-gray-900 text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block w-full p-2.5 dark:bg-gray-700 dark:border-gray-600 dark:placeholder-gray-400 dark:text-white"
                               ${isRequired ? 'required' : ''}>`;
            }
            
            // Add date-field class and initial display state
            const isDateField = field.name === 'start_date' || field.name === 'end_date';
            const initialDisplay = parkingData.status === 'Booked' && !parkingData.booking ? 'block' : 'none';
            
            return `
                <div class="mb-4">
                    <label for="${field.name}" class="block mb-2 text-sm font-medium text-gray-900 dark:text-white">
                        ${field.label}${isRequired ? ' *' : ''}
                    </label>
                    ${inputElement}
                    ${isRequired ? `
                    <p class="mt-1 text-sm text-red-600 dark:text-red-400 hidden" id="${field.name}-error">
                        This field is required
                    </p>` : ''}
                </div>`;
        }).join('');
    }

    function handleStatusChange(status) {
        const dateFields = document.querySelectorAll('.date-field');
        const bookingField = document.getElementById('booking');
        
        if (status === 'Booked') {
            if (!bookingField.value) {
                // Only show and require dates if no booking is selected
                dateFields.forEach(field => {
                    field.style.display = 'block';
                    field.querySelector('input').required = true;
                    field.querySelector('input').readOnly = false;
                });
            }
        } else {
            // For non-booked status, hide dates and clear values
            dateFields.forEach(field => {
                field.style.display = 'none';
                const input = field.querySelector('input');
                input.required = false;
                input.value = '';
                input.readOnly = false;
            });
        }
    }

    function validateForm(form) {
        const requiredFields = ['apartment_number', 'parking_number'];
        let isValid = true;
        
        // Add date validation for booked status without booking
        const status = form.querySelector('[name="status"]').value;
        const booking = form.querySelector('[name="booking"]').value;
        if (status === 'Booked' && !booking) {
            requiredFields.push('start_date', 'end_date');
        }
        
        requiredFields.forEach(fieldName => {
            const field = form.querySelector(`[name="${fieldName}"]`);
            if (!field) {
                console.error(`Field ${fieldName} not found in form`);
                return;
            }
            
            const errorElement = document.getElementById(`${fieldName}-error`);
            
            if (!field.value.trim()) {
                isValid = false;
                field.classList.add('border-red-500');
                errorElement?.classList.remove('hidden');
            } else {
                field.classList.remove('border-red-500');
                errorElement?.classList.add('hidden');
            }
        });
        
        // Validate date range if both dates are provided
        const startDate = form.querySelector('[name="start_date"]')?.value;
        const endDate = form.querySelector('[name="end_date"]')?.value;
        if (startDate && endDate) {
            const start = new Date(startDate);
            const end = new Date(endDate);
            if (start > end) {
                isValid = false;
                alert('End date must be after start date');
            }
        }
        
        return isValid;
    }

    function addFormSubmitHandler(form) {
        form.addEventListener('submit', function(e) {
            e.preventDefault();
            
            if (!validateForm(this)) {
                return;
            }
            
            // Log form data before submit
            const formData = new FormData(this);
            console.log('Form Data:');
            for (let pair of formData.entries()) {
                console.log(pair[0] + ': ' + pair[1]);
            }
            
            // Submit the form traditionally
            this.submit();
        });
    }

    function openModalForDay(id) {
        const selectedParking = getParkingById(id);
        if (!selectedParking) return;

        const modal = document.getElementById('parkingModal');
        const parkingList = document.getElementById('parkingList');
        const modalDate = document.getElementById('modalDate');
        
        modalDate.textContent = `Parking Details - ${selectedParking.apartment_name}`;
        modalDate.setAttribute('data-number', selectedParking.apartment_number);
        
        // Create form with model fields
        parkingList.innerHTML = `
            <form id="parkingForm" method="POST" action="${window.location.href}" class="space-y-4">
                <input type="hidden" name="id" value="${selectedParking.id}">
                <input type="hidden" name="edit" value="true">
                ${createFormFields(model_fields_json, selectedParking)}
                <div class="flex justify-end space-x-2">
                    <button type="button" 
                            onclick="handleDelete(${selectedParking.id})"
                            class="px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500">
                        Delete
                    </button>
                    <button type="button" onclick="closeModal()" 
                            class="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 focus:outline-none focus:ring-2 focus:ring-gray-400">
                        Cancel
                    </button>
                    <button type="submit" 
                            class="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500">
                        Save Changes
                    </button>
                </div>
            </form>`;

        // Initialize form state based on current values
        const form = document.getElementById('parkingForm');
        if (form) {
            const status = form.querySelector('[name="status"]').value;
            const booking = form.querySelector('[name="booking"]').value;
            if (booking) {
                handleBookingChange(booking);
            } else {
                handleStatusChange(status);
            }
        }

        // Add form submit handler
        addFormSubmitHandler(document.getElementById('parkingForm'));
        
        modal.classList.remove('hidden');
    }

    function closeModal() {
        document.getElementById('parkingModal').classList.add('hidden');
    }

    // Move all popover-related code inside DOMContentLoaded
    document.addEventListener('DOMContentLoaded', function() {
        // Add event listener for close button
        const closePopoverButton = document.getElementById('close-popover');
        if (closePopoverButton) {
            closePopoverButton.addEventListener('click', hidePopover);
        }

        // Prevent popover from closing when clicking inside it
        const dynamicPopover = document.getElementById('dynamic-popover');
        if (dynamicPopover) {
            dynamicPopover.addEventListener('mouseenter', (e) => {
                e.stopPropagation();
            });
        }

        // Initialize popover listeners
        addPopoverListeners();
    });

    // Keep these functions outside but available globally
    function showPopover(event, dateStr) {
        const bookings = getBookingsForDate(dateStr);
        if (!bookings || bookings.length === 0) return;

        const popover = document.getElementById('dynamic-popover');
        const title = document.getElementById('popover-title');
        const content = document.getElementById('popover-content');

        if (!popover || !title || !content) return;

        // Set title
        const date = new Date(dateStr);
        title.textContent = date.toLocaleDateString('en-US', { 
            month: 'long', 
            day: 'numeric',
            year: 'numeric'
        });

        // Generate content - simplified to show just "Booked: StartTime - EndTime"
        content.innerHTML = bookings.map(booking => `
            <div class="py-1">
                <div class="text-gray-700">Booked: ${booking.start_time} - ${booking.end_time}</div>
            </div>
        `).join('');

        // Position popover
        popover.style.left = `${event.pageX + 10}px`;
        popover.style.top = `${event.pageY + 10}px`;
        popover.classList.remove('hidden');
    }

    function hidePopover() {
        const popover = document.getElementById('dynamic-popover');
        if (popover) {
            popover.classList.add('hidden');
        }
    }

    function addPopoverListeners() {
        const bookedDays = document.querySelectorAll('[data-has-bookings="true"]');
        bookedDays.forEach(day => {
            day.addEventListener('mouseenter', (e) => {
                const dateStr = day.getAttribute('data-date');
                showPopover(e, dateStr);
            });
            day.addEventListener('mouseleave', hidePopover);
        });
    }

    // Add new function to create building sections
    function createBuildingSections() {
        const container = document.getElementById('buildingsContainer');
        
        // Sort building numbers
        const buildingNumbers = Object.keys(parkingData).sort((a, b) => {
            return parseInt(a) - parseInt(b);
        });

        // Add create button at the top
        const createButtonSection = document.createElement('div');
        createButtonSection.className = 'flex justify-end mb-4';
        createButtonSection.innerHTML = `
            <button onclick="openCreateModal()"
                    class="px-4 py-2 text-sm font-medium text-white bg-green-600 rounded-lg hover:bg-green-700 focus:outline-none focus:ring-2 focus:ring-green-500">
                Create New Parking Record
            </button>
        `;
        container.appendChild(createButtonSection);

        buildingNumbers.forEach(buildingNumber => {
            const parkings = parkingData[buildingNumber];
            
            const buildingSection = document.createElement('div');
            buildingSection.className = 'bg-white dark:bg-gray-800 rounded-lg shadow p-6';
            
            buildingSection.innerHTML = `
                <h3 class="text-2xl font-bold mb-4">Building ${buildingNumber}</h3>
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                    ${parkings.map(parking => `
                        <div class="border rounded-lg p-4 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700"
                             onclick="openModalForDay(${parking.id})">
                            <div class="flex justify-between items-center mb-2">
                                <div>
                                    <span class="font-semibold">Apt ${parking.apartment_number}</span>
                                    <span class="ml-2 px-2 py-1 rounded-full text-sm ${getStatusClass(parking.status)}">
                                        ${parking.status || 'Unknown'}
                                    </span>
                                </div>
                            </div>
                            <div class="text-sm text-gray-600 dark:text-gray-300">
                                ${formatParkingInfo(parking)}
                            </div>
                        </div>
                    `).join('')}
                </div>
            `;
            
            container.appendChild(buildingSection);
        });
    }

    function getStatusClass(status) {
        const statusClasses = {
            'available': 'bg-green-100 text-green-800',
            'occupied': 'bg-red-100 text-red-800',
            'reserved': 'bg-yellow-100 text-yellow-800',
            'maintenance': 'bg-gray-100 text-gray-800'
        };
        return statusClasses[status?.toLowerCase()] || 'bg-gray-100 text-gray-800';
    }

    function formatParkingInfo(parking) {
        let info = '';
        
        // Add parking number with accent color
        info += `<p class="text-blue-600 dark:text-blue-400 font-medium mb-1">Parking #${parking.parking_number || 'N/A'}</p>`;
        
        if (!parking.tenant_name && !parking.start_date) {
            info += '<p>No current booking/reservation</p>';
            return info;
        }

        if (parking.tenant_name) {
            info += `<p class="truncate">${parking.tenant_name}</p>`;
        }
        if (parking.start_date && parking.end_date) {
            info += `<p class="text-gray-500">${formatDates(parking.start_date, parking.end_date)}</p>`;
            if (!parking.tenant_name) {
                info += '<p class="text-sm text-gray-400">(Reserved)</p>';
            }
        }
        return info;
    }

    function formatDates(startDate, endDate) {
        if (!startDate || !endDate) return 'N/A';
        return `${new Date(startDate).toLocaleDateString()} - ${new Date(endDate).toLocaleDateString()}`;
    }

    // Add new function to handle delete
    function handleDelete(id) {
        if (confirm('Are you sure you want to delete this parking record?')) {
            const form = document.getElementById('parkingForm');
            const formData = new FormData(form);
            
            // Log form data
            console.log('Delete Form Data:');
            for (let pair of formData.entries()) {
                console.log(pair[0] + ': ' + pair[1]);
            }
            
            formData.delete('edit');
            formData.append('delete', 'true');
            
            // Create and submit a form for delete
            const deleteForm = document.createElement('form');
            deleteForm.method = 'POST';
            deleteForm.action = window.location.href;
            
            for (let pair of formData.entries()) {
                const input = document.createElement('input');
                input.type = 'hidden';
                input.name = pair[0];
                input.value = pair[1];
                deleteForm.appendChild(input);
            }
            
            document.body.appendChild(deleteForm);
            deleteForm.submit();
        }
    }

    // Add this new function
    function openCreateModal() {
        const modal = document.getElementById('parkingModal');
        const parkingList = document.getElementById('parkingList');
        const modalDate = document.getElementById('modalDate');
        
        modalDate.textContent = 'Create New Parking Record';
        
        // Create empty form for new parking
        parkingList.innerHTML = `
            <form id="parkingForm" method="POST" class="space-y-4">
                <input type="hidden" name="add" value="true">
                ${createFormFields(model_fields_json, {})}
                <div class="flex justify-end space-x-2">
                    <button type="button" onclick="closeModal()" 
                            class="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 focus:outline-none focus:ring-2 focus:ring-gray-400">
                        Cancel
                    </button>
                    <button type="submit" 
                            class="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500">
                        Create
                    </button>
                </div>
            </form>`;

        // Remove the form submit handler since we want native form submission
        const form = document.getElementById('parkingForm');
        form.addEventListener('submit', function(e) {
            if (!validateForm(this)) {
                e.preventDefault();
                return;
            }
        });
        
        modal.classList.remove('hidden');
    }

    // Add new function to filter bookings by apartment
    function filterBookingsByApartment(apartmentNumber) {
        if (!apartmentNumber) return;
        
        // Find all bookings for this apartment
        let apartmentBookings = [];
        for (const [buildingNumber, parkings] of Object.entries(parkingData)) {
            const filtered = parkings.filter(p => p.apartment_number === apartmentNumber);
            apartmentBookings = apartmentBookings.concat(filtered);
        }
        
        // Update the bookings display in the modal
        const bookingsContainer = document.createElement('div');
        bookingsContainer.className = 'mt-4 border-t pt-4';
        bookingsContainer.innerHTML = `
            <h4 class="text-lg font-medium mb-2">Existing Bookings for Apartment ${apartmentNumber}</h4>
            ${apartmentBookings.length > 0 ? `
                <div class="space-y-2">
                    ${apartmentBookings.map(booking => `
                        <div class="p-3 bg-gray-50 rounded-lg">
                            <div class="flex justify-between items-center">
                                <div>
                                    <p class="font-medium">Parking #${booking.parking_number}</p>
                                    <p class="text-sm text-gray-600">
                                        ${booking.start_date ? `${formatDates(booking.start_date, booking.end_date)}` : 'No dates specified'}
                                    </p>
                                </div>
                                <span class="px-2 py-1 rounded-full text-sm ${getStatusClass(booking.status)}">
                                    ${booking.status || 'Unknown'}
                                </span>
                            </div>
                        </div>
                    `).join('')}
                </div>
            ` : '<p class="text-gray-500">No existing bookings found for this apartment</p>'}
        `;
        
        // Find or create the bookings display container
        let existingBookingsContainer = document.getElementById('apartment-bookings');
        if (!existingBookingsContainer) {
            existingBookingsContainer = document.createElement('div');
            existingBookingsContainer.id = 'apartment-bookings';
            document.getElementById('parkingList').appendChild(existingBookingsContainer);
        }
        
        // Update the container content
        existingBookingsContainer.innerHTML = '';
        existingBookingsContainer.appendChild(bookingsContainer);
    }

    // Call the function when the document is loaded
    document.addEventListener('DOMContentLoaded', function() {
        createBuildingSections();
        // Keep existing DOMContentLoaded handlers...
    });
</script>

<div id="dynamic-popover" class="hidden fixed z-10 w-[375px] text-sm text-gray-500 bg-white border border-gray-200 rounded-lg shadow-sm dark:text-gray-400 dark:border-gray-600 dark:bg-gray-800">
    <div class="px-3 py-2 bg-gray-100 border-b border-gray-200 rounded-t-lg dark:border-gray-600 dark:bg-gray-700 flex justify-between items-center">
        <h3 class="font-semibold text-gray-900 dark:text-white" id="popover-title"></h3>
        <button id="close-popover" class="text-gray-600 hover:text-gray-800 dark:text-gray-300 dark:hover:text-white">
            &times;
        </button>
    </div>
    <div class="px-3 py-2" id="popover-content">
        <!-- Content will be dynamically added here -->
    </div>
</div>


<div id="parkingModal" class="hidden fixed inset-0 bg-black bg-opacity-75 overflow-y-auto h-full w-full z-50 flex items-center justify-center">
    <div class="relative p-6 border w-full max-w-2xl shadow-lg rounded-md bg-white">
        <div class="flex flex-col">
            <div class="flex justify-between items-center mb-4">
                <h3 class="text-xl font-semibold text-gray-900" id="modalDate"></h3>
                <button onclick="closeModal()" class="text-gray-500 hover:text-gray-700">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                </button>
            </div>
            
            <div id="parkingList" class="mb-4">
                <!-- Parking information will be inserted here -->
            </div>
        </div>
    </div>
</div>

{% endblock content %}


from django.shortcuts import render
from ..models import Apartment, Booking, Cleaning, Payment, User, HandymanCalendar, ApartmentParking
import logging
from mysite.forms import BookingForm, ApartmentParkingForm  
from django.db.models import Q
import json
from datetime import date, timedelta
from collections import defaultdict
from django.utils import timezone
from dateutil.relativedelta import relativedelta
from ..decorators import user_has_role
from .utils import generate_weeks, DateEncoder, handle_post_request
from mysite.forms import HandymanCalendarForm
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from mysite.forms import CustomFieldMixin

def get_model_fields(form):
    """
    Convert form fields into a JSON-serializable format.
    Specifically designed for the parking calendar form fields.
    """
    serializable_fields = []
    
    for field_name, field_instance in form.fields.items():
        if isinstance(field_instance, CustomFieldMixin):
            field_data = {
                'name': field_name,
                'label': field_instance.label or field_name.replace('_', ' ').title(),
                'type': field_instance.__class__.__name__,
                'required': field_instance.required,
                'order': getattr(field_instance, 'order', 0),
                'ui_element': getattr(field_instance, 'ui_element', 'input'),
            }
            
            # Handle choices if the field has them
            if hasattr(field_instance, 'choices') and field_instance.choices:
                # Handle dropdown options
                dropdown_options = field_instance._dropdown_options
                if callable(dropdown_options):
                    field_data['choices'] = dropdown_options()
                else:
                    field_data['choices'] = dropdown_options

                # Set ui_element to 'select' if it has choices and no specific ui_element
                if 'ui_element' not in field_data:
                    field_data['ui_element'] = 'select'
            
            serializable_fields.append(field_data)
    
    # Sort fields by order
    serializable_fields.sort(key=lambda x: x['order'])
    return serializable_fields

def prepare_form_fields(request):
    """
    Prepare form fields data for the parking calendar.
    Returns JSON serializable form fields data.
    """
    form = ApartmentParkingForm(request=request)
    model_fields = get_model_fields(form)
    return json.dumps(model_fields, cls=DateEncoder)

def parking_calendar(request):
    status = request.GET.get('status',None)
    if status:
        parking_data = ApartmentParking.get_filtered_parking(status=status)
    else:
        parking_data = ApartmentParking.get_all_parking()

    if request.method == 'POST':
        handle_post_request(request, ApartmentParking, ApartmentParkingForm)

    # Group parking data by apartment number
    grouped_parking = defaultdict(list)
    for parking in parking_data:
        # Get apartment building number with fallback
        building_n = getattr(parking.apartment, 'building_n', None)
        if building_n is None:
            continue  # Skip if no building number

        parking_dict = {
            'id': parking.id,
            'status': parking.status or None,
            'parking_number': parking.parking_number or None,
            'building_n': getattr(parking.apartment, 'name', None),
            'apartment_number': getattr(parking.apartment, 'apartment_n', None),
            'apartment_name': getattr(parking.apartment, 'name', None),
            'apartment': getattr(parking.apartment, 'id', None),
            'notes': getattr(parking, 'notes', None),
        }

        # Add booking-related information
        if hasattr(parking, 'booking') and parking.booking:
            parking_dict.update({
                'start_date': parking.booking.start_date if hasattr(parking.booking, 'start_date') else None,
                'booking_id': parking.booking.id if hasattr(parking.booking, 'id') else None,
                'end_date': parking.booking.end_date if hasattr(parking.booking, 'end_date') else None,
                'tenant_name': parking.booking.tenant.full_name if (hasattr(parking.booking, 'tenant') and 
                                                                  hasattr(parking.booking.tenant, 'full_name')) else None,
                'tenant_phone': parking.booking.tenant.phone if (hasattr(parking.booking, 'tenant') and 
                                                               hasattr(parking.booking.tenant, 'phone')) else None,
            })
        else:
            parking_dict.update({
                'start_date': None,
                'end_date': None,
                'tenant_name': None,
                'tenant_phone': None,
            })

        grouped_parking[building_n].append(parking_dict)

    # Convert the defaultdict to regular dict for JSON serialization
    grouped_parking_dict = dict(grouped_parking)
    parking_data_json = json.dumps(grouped_parking_dict, cls=DateEncoder)

    # Get form fields data
    form_fields = prepare_form_fields(request)

    context = {
        'parking_data': parking_data,
        'parking_data_json': parking_data_json,
        'title': "Parking Calendar",
        'endpoint': "/parking_calendar",
        'model_fields_json': form_fields,
    }

    return render(request, 'parking_calendar.html', context)